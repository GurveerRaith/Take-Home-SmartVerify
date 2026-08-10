// Staging is permissive: any authenticated principal may act.
permit(principal, action, resource) when { context.authenticated == true };
