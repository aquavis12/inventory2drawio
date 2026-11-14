// The same role may render diagrams from graphs the collector saved.
permit(
  principal is AgentCore::IamEntity,
  action == AgentCore::Action::"render-drawio___render_drawio",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when {
  principal.id like "{{RUNTIME_ROLE_PATTERN}}"
};
