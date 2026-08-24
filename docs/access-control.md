# Access Control

Ascend enforces access control in three layers. Identity is established by
`get_current_active_user`, which resolves an authenticated, active user. Endpoint capability
is enforced by `require_roles`; its explicit ADMIN override permits administrators through every
role gate. Service record scope requires the acting user so each service can restrict the records
that actor may access.

Each user has one effective `EmployeeRole`. A group assigns that role to its users; it does not
provide a second authorization grant or per-permission override. The role and group assignment
are retained separately so administrative role assignment remains available.

Frontend route visibility is not enforcement. Every backend endpoint and service must enforce
identity, endpoint capability, and record scope independently of what the client chooses to show.
