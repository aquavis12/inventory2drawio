// Only this agent's Runtime role may read inventory, and only in approved Regions.
// The Region check runs at the Gateway: a prompt cannot talk its way around it.
permit(
  principal is AgentCore::IamEntity,
  action == AgentCore::Action::"collect-inventory___collect_inventory",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when {
  principal.id like "{{RUNTIME_ROLE_PATTERN}}" &&
  context.input has region &&
  [{{ALLOWED_REGIONS}}].contains(context.input.region)
};
