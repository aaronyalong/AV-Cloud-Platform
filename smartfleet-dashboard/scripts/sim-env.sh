#!/usr/bin/env bash
# Usage:  source scripts/sim-env.sh   -> points the simulator at the deployed stack
STACK=SmartFleetDashboard
export SIM_API=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" --output text)
ARN=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='SimKeySecretArn'].OutputValue" --output text)
export SIM_API_KEY=$(aws secretsmanager get-secret-value --secret-id "$ARN" --query SecretString --output text)
echo "SIM_API=$SIM_API  (SIM_API_KEY loaded)"
