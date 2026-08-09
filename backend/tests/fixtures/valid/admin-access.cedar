// Administrators may perform any action on any resource.
permit(
  principal in Group::"administrators",
  action,
  resource
);
