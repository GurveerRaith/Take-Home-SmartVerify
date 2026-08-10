// Analysts may view reports, but only ones they own.
permit(
  principal in Group::"analysts",
  action == Action::"view",
  resource in Folder::"reports"
) when { resource.owner == principal };
