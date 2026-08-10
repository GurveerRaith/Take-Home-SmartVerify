// Initech administrators. Stored under the same filename as the Globex
// policy, deliberately: the two tenants must stay completely separate.
permit(
  principal in Group::"initech-admins",
  action,
  resource
);

// Multi-factor authentication is mandatory.
forbid(principal, action, resource) when { context.mfa == false };
