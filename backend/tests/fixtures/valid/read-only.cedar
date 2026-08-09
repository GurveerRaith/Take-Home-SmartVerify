// Alice may view reports she owns.
permit(
  principal == User::"alice",
  action == Action::"view",
  resource in Folder::"reports"
) when { resource.owner == principal };
