// Globex administrators may perform any action on any resource.
permit(
  principal in Group::"globex-admins",
  action,
  resource
);
