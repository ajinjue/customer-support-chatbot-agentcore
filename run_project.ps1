<#
  One-command-per-step runner for the Customer Support Chatbot project (Windows).
  Run from repo\project\starter:

    .\run_project.ps1 -Step check        # verify AWS credentials + region
    .\run_project.ps1 -Step setup        # venv + dependencies
    .\run_project.ps1 -Step deploy       # tool stack (DynamoDB + Lambda + IAM) + gateway
    .\run_project.ps1 -Step harness      # create/update the harness from system_prompt.txt
    .\run_project.ps1 -Step chat         # interactive chat.py session
    .\run_project.ps1 -Step transcripts  # record the 4 rubric transcripts
    .\run_project.ps1 -Step tickets      # list tickets in DynamoDB
    .\run_project.ps1 -Step dataset      # generate output_eval_dataset.jsonl
    .\run_project.ps1 -Step evaljob      # testing stack + S3 upload + Bedrock evaluation job
    .\run_project.ps1 -Step evalstatus   # job status; when Completed, download per-record scores
    .\run_project.ps1 -Step package      # build ..\..\..\submission.zip
    .\run_project.ps1 -Step cleanup      # delete everything (ONLY after you have submitted)
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('check','setup','deploy','harness','chat','transcripts','tickets','dataset','evaljob','evalstatus','package','cleanup')]
  [string]$Step,
  [string]$JobName = 'support-chatbot-eval-run-1'
)

$ErrorActionPreference = 'Stop'
$env:AWS_DEFAULT_REGION = 'us-east-1'
$env:PYTHONIOENCODING = 'utf-8'
$Region = 'us-east-1'
Set-Location $PSScriptRoot

function Py { if (Test-Path .\venv\Scripts\python.exe) { & .\venv\Scripts\python.exe @args } else { & python @args }; if ($LASTEXITCODE) { throw "python $args failed ($LASTEXITCODE)" } }
function Aws { & aws.exe @args; if ($LASTEXITCODE) { throw "aws $($args[0..1] -join ' ') failed ($LASTEXITCODE)" } }
function StackOutput($stack, $key) {
  aws.exe cloudformation describe-stacks --stack-name $stack --region $Region --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text
}

switch ($Step) {
  'check' {
    Aws sts get-caller-identity --output table
    Write-Host "Region forced to $Region for every step." -ForegroundColor Green
  }
  'setup' {
    if (-not (Test-Path .\venv)) { python -m venv venv }
    & .\venv\Scripts\python.exe -m pip install --upgrade pip -q
    & .\venv\Scripts\python.exe -m pip install -r requirements.txt -q
    Py -c "import boto3; print('boto3', boto3.__version__)"
  }
  'deploy' {
    Aws cloudformation deploy --template-file cloudformation-tool.yaml --stack-name bug-report-tool-stack --capabilities CAPABILITY_NAMED_IAM --region $Region
    try { Py setup_gateway.py } catch { Write-Host 'Gateway setup failed (often IAM propagation). Retrying in 60s...' -ForegroundColor Yellow; Start-Sleep 60; Py setup_gateway.py }
  }
  'harness' { Py create_harness.py }
  'chat' { Py run_with_fresh_actor.py chat.py }
  'transcripts' { Py run_transcripts.py }
  'tickets' {
    Aws dynamodb scan --table-name bug-report-tool-stack-bug-reports --region $Region --query 'Items[].{ticketId:ticketId.S,description:description.S,environment:environment.S}' --output table
  }
  'dataset' {
    Py run_with_fresh_actor.py generate-eval-dataset.py --tests-json harness-tests.json
    $errs = Select-String -Path output_eval_dataset.jsonl -Pattern 'HARNESS_ERROR' -SimpleMatch
    if ($errs) { Write-Host "WARNING: $($errs.Count) [HARNESS_ERROR] line(s) - fix and re-run before submitting." -ForegroundColor Red }
    else { Write-Host 'No [HARNESS_ERROR] entries. Dataset is good.' -ForegroundColor Green }
  }
  'evaljob' {
    Aws cloudformation deploy --template-file cloudformation-testing.yaml --stack-name bug-report-testing-stack --capabilities CAPABILITY_NAMED_IAM --region $Region
    $bucket = StackOutput 'bug-report-testing-stack' 'EvalDatasetBucketName'
    $role   = StackOutput 'bug-report-testing-stack' 'BedrockEvalRoleArn'
    Write-Host "Bucket: $bucket`nRole:   $role"
    Aws s3 cp output_eval_dataset.jsonl "s3://$bucket/output_eval_dataset.jsonl" --region $Region
    $evalCfg = @{ automated = @{
        datasetMetricConfigs = @(@{ taskType = 'General'
          dataset = @{ name = 'support-chatbot-eval-dataset'; datasetLocation = @{ s3Uri = "s3://$bucket/output_eval_dataset.jsonl" } }
          metricNames = @('Builtin.Correctness') })
        evaluatorModelConfig = @{ bedrockEvaluatorModels = @(@{ modelIdentifier = 'amazon.nova-pro-v1:0' }) } } }
    $infCfg = @{ models = @(@{ precomputedInferenceSource = @{ inferenceSourceIdentifier = 'my-support-chatbot' } }) }
    $outCfg = @{ s3Uri = "s3://$bucket/results/" }
    # Write JSON to files to avoid PowerShell quoting problems with inline JSON.
    $evalCfg | ConvertTo-Json -Depth 10 | Set-Content -Encoding ascii eval-config.json
    $infCfg  | ConvertTo-Json -Depth 10 | Set-Content -Encoding ascii inference-config.json
    $outCfg  | ConvertTo-Json -Depth 10 | Set-Content -Encoding ascii output-config.json
    Aws bedrock create-evaluation-job --job-name $JobName --role-arn $role `
      --evaluation-config file://eval-config.json --inference-config file://inference-config.json `
      --output-data-config file://output-config.json --region $Region
    Write-Host 'Job created. It usually takes 10-30 minutes. Check with: .\run_project.ps1 -Step evalstatus' -ForegroundColor Green
  }
  'evalstatus' {
    $arn = aws.exe bedrock list-evaluation-jobs --region $Region --name-contains $JobName --query 'jobSummaries[0].jobArn' --output text
    $status = aws.exe bedrock get-evaluation-job --job-identifier $arn --region $Region --query 'status' --output text
    Write-Host "Job $JobName status: $status"
    if ($status -eq 'Completed') {
      $bucket = StackOutput 'bug-report-testing-stack' 'EvalDatasetBucketName'
      New-Item -ItemType Directory -Force eval_results | Out-Null
      Aws s3 cp "s3://$bucket/results/" eval_results --recursive --region $Region
      Py summarize_eval.py eval_results
      Write-Host 'Now open the job in the Bedrock console (Evaluations) and take the results-page screenshot.' -ForegroundColor Green
    }
  }
  'package' {
    $root = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
    $pkg = Join-Path $root 'submission'
    if (Test-Path $pkg) { Remove-Item -Recurse -Force $pkg }
    New-Item -ItemType Directory -Force $pkg, "$pkg\transcripts", "$pkg\screenshots" | Out-Null
    $required = 'system_prompt.txt','agentcore_config.json','harness-tests.json','output_eval_dataset.jsonl','README.md'
    foreach ($f in $required) { if (Test-Path $f) { Copy-Item $f $pkg } else { Write-Host "MISSING: $f" -ForegroundColor Red } }
    foreach ($f in 'eval_scores.md','online_shop_faq.md','run_transcripts.py','fresh_actor.py','run_with_fresh_actor.py') { if (Test-Path $f) { Copy-Item $f $pkg } }
    if (Test-Path transcripts) { Copy-Item transcripts\*.txt "$pkg\transcripts" } else { Write-Host 'MISSING: transcripts\' -ForegroundColor Red }
    $shots = Get-ChildItem screenshots -File -ErrorAction SilentlyContinue
    if ($shots) { $shots | Copy-Item -Destination "$pkg\screenshots" } else { Write-Host 'MISSING: screenshots\ (DynamoDB table + evaluation results page)' -ForegroundColor Red }
    if (Select-String -Path system_prompt.txt -Pattern '^# ' -Quiet) { Write-Host 'WARNING: system_prompt.txt still has starter comment lines.' -ForegroundColor Yellow }
    if (Select-String -Path README.md -Pattern 'TODO' -SimpleMatch -Quiet) { Write-Host 'WARNING: README.md still contains TODO placeholders.' -ForegroundColor Yellow }
    $zip = Join-Path $root 'submission.zip'
    if (Test-Path $zip) { Remove-Item $zip }
    # Python zip: forward-slash paths so folders survive on macOS/Linux (Compress-Archive in PS 5.1 writes backslashes).
    python -c "import zipfile,pathlib,sys; r=pathlib.Path(sys.argv[1]); z=zipfile.ZipFile(sys.argv[2],'w',zipfile.ZIP_DEFLATED); [z.write(p,p.relative_to(r).as_posix()) for p in sorted(r.rglob('*')) if p.is_file()]; z.close()" $pkg $zip
    Write-Host "Built $zip" -ForegroundColor Green
    Get-ChildItem -Recurse $pkg | Select-Object FullName, Length | Format-Table -AutoSize
  }
  'cleanup' {
    Py cleanup_agentcore.py
    $bucket = StackOutput 'bug-report-testing-stack' 'EvalDatasetBucketName'
    if ($bucket -and $bucket -ne 'None') { Aws s3 rm "s3://$bucket" --recursive --region $Region }
    Aws cloudformation delete-stack --stack-name bug-report-testing-stack --region $Region
    Aws cloudformation delete-stack --stack-name bug-report-tool-stack --region $Region
    Write-Host 'Cleanup requested. Stacks delete in the background.' -ForegroundColor Green
  }
}
