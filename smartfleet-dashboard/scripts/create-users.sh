#!/usr/bin/env bash
# Creates the 7 demo users in Cognito (same list as backend/app/seed.py) with a permanent password.
set -euo pipefail
STACK=SmartFleetDashboard
POOL=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
PASSWORD="${DEMO_PASSWORD:-SmartFleet2026}"
while IFS=, read -r EMAIL TENANT ROLE NAME; do
  echo "-> $EMAIL ($ROLE, $TENANT)"
  aws cognito-idp admin-create-user --user-pool-id "$POOL" --username "$EMAIL" --message-action SUPPRESS \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true Name=name,Value="$NAME" \
                      Name=custom:tenant_id,Value="$TENANT" >/dev/null 2>&1 || echo "   (already exists)"
  aws cognito-idp admin-set-user-password --user-pool-id "$POOL" --username "$EMAIL" \
    --password "$PASSWORD" --permanent
  aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username "$EMAIL" --group-name "$ROLE"
done <<'USERS'
admin@example.com,t01,platform_admin,Priya Patel
tadmin@example.com,t01,tenant_admin,Tom Nguyen
operator@example.com,t01,fleet_operator,Jordan Rivera
support@example.com,t01,support_staff,Mei Chen
owner@example.com,t01,vehicle_owner,Owen Brooks
ai@example.com,t01,ai_engineer,Ana Lopez
operator2@example.com,t02,fleet_operator,Sam Ortiz
USERS
echo "Done. Password for every user: $PASSWORD"
