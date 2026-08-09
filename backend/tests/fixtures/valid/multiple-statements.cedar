// A file may contain more than one statement.
permit(principal, action, resource);

// Deny anyone who has not completed multi-factor authentication.
forbid(principal, action, resource) when { context.mfa == false };
