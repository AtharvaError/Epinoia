// Role-based access control.
// Roles: director | lead | volunteer | readonly.
// Sensitive field classes (contact, medical, accessibility, room) are gated:
//   - redacted for insufficient roles in views AND in audit output
//   - only authorized roles may approve HARD-constraint / budget overrides
// TODO: can(role, action, resource) + field-level redaction helper.
export {};
