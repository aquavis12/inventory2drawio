// Web search is allowed but capped: at most 5 results per query keeps cost and
// context small. Which sites it may search is fixed on the target, not here.
permit(
  principal is AgentCore::IamEntity,
  action == AgentCore::Action::"web-search___WebSearch",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when {
  principal.id like "{{RUNTIME_ROLE_PATTERN}}" &&
  (!(context.input has maxResults) || context.input.maxResults <= 5)
};
