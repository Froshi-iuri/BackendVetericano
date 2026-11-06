# Role Overview – Backend Documentation

## 1. Data Model

- **File:** `BackendVetericano/users/models.py`
- **Model:** `Rol`
- **Fields:**
  ```python
  class Rol(models.Model):
      id_rol = models.AutoField(primary_key=True)
      nombre_rol = models.CharField(max_length=50)
  ```
- The table is **managed = False** (it exists in the external production DB and is not created/altered by Django migrations).

## 2. Default Role Records

| `id_rol` | `nombre_rol`       | Description                              |
|----------|--------------------|------------------------------------------|
| 1        | Administrador      | Full‑access admin of the system           |
| 2        | Jurídico           | Legal‑team users                         |
| 3        | Veterinario        | Clinical staff                           |
| 4*       | Peticionario       | Requestor – created automatically on user registration |

> **Note:** Role 4 is not pre‑populated in the database; it is created on‑the‑fly by the serializer when a user without an existing role is saved.

## 3. Relation to Users

- **File:** `BackendVetericano/users/models.py`
- **Model:** `Usuario`
- **Key field:** `id_rol = models.ForeignKey(Rol, on_delete=models.PROTECT, related_name="usuarios")`
- Each user has a foreign‑key to `Rol`. The FK guarantees referential integrity – a user cannot be saved with a non‑existent role.

## 4. API Exposure

### Serializers
- **File:** `BackendVetericano/users/api/serializers.py`
- **`UsuariosSerializer`**
  ```python
  class UsuariosSerializer(serializers.ModelSerializer):
      nombre_rol = serializers.CharField(source='id_rol.nombre_rol', read_only=True)
      class Meta:
          model = Usuario
          fields = ('id', 'username', 'email', 'activo', 'id_rol', 'nombre_rol', ...)
  ```
- The serializer adds a **read‑only** field `nombre_rol` that pulls the role name directly from the related `Rol` record.

### Views / Endpoints
- **File:** `BackendVetericano/users/api/views.py`
- **Endpoint:** `GET /api/usuarios/usuarios/`
- Returns a list of users where each object contains both:
  - `id_rol` (numeric FK)
  - `nombre_rol` (string name)
- Example JSON fragment:
  ```json
  {
    "id": 12,
    "username": "juanp",
    "activo": true,
    "id_rol": 3,
    "nombre_rol": "Veterinario"
  }
  ```

### Token Payload
- In the login view a JWT token is built with:
  ```python
  payload = {
      'user_id': user.id,
      'id_rol': user.id_rol.id_rol,
      'nombre_rol': user.id_rol.nombre_rol,
      ...
  }
  ```
- The frontend can decode the token to know the current user's role without an extra call.

## 5. Role Constants (Frontend Sync)
- **File:** `BackendVetericano/inventario/constants.py`
  ```python
  ROL_ADMINISTRADOR = 1
  ROL_JURIDICO = 2
  ROL_VETERINARIO = 3
  # Peticionario (4) is created dynamically
  ```
- These constants are used by the Angular frontend to map numeric IDs to human‑readable labels and UI colors.

## 6. Permissions & Access Control

- Django **permissions** are defined in `BackendVetericano/users/permissions.py` (if present) and are typically based on `id_rol`.
- Example snippet:
  ```python
  class IsAdmin(permissions.BasePermission):
      def has_permission(self, request, view):
          return request.user.id_rol_id == ROL_ADMINISTRADOR
  ```
- Views that modify critical data (`/api/usuarios/*`) are protected with such role‑based checks.

## 7. How Roles Appear in the Frontend

- The Angular service `auth.service.ts` calls `listUsersActive()` which now returns the full user objects, including `nombre_rol`.
- Components (`graficas`, `usuarios‑roles`) use a **normalizer**:
  ```typescript
  function normalizeRole(user: any): string {
    const roleMap = {
      1: 'Administrador',
      2: 'Jurídico',
      3: 'Veterinario',
      4: 'Peticionario',
    };
    if (typeof user.id_rol === 'number') {
      return roleMap[user.id_rol] ?? 'Indefinido';
    }
    return (user.nombre_rol || '').toLowerCase();
  }
  ```
- This logic guarantees the UI shows correct counts even when the backend sends only the numeric ID.

## 8. Summary Table (Quick Reference)

| Role ID | Role Name (Spanish) | UI Badge Color (hex) |
|---------|---------------------|----------------------|
| 1       | Administrador       | `#4141A5` (Indigo)   |
| 2       | Jurídico            | `#5A67D8` (Indigo‑400) |
| 3       | Veterinario         | `#667EEA` (Indigo‑500) |
| 4       | Peticionario        | `#9F7AEA` (Purple‑400) |

---

**Key Takeaways**
- Roles are stored in a dedicated `Rol` table (managed = False).
- Four logical roles are used; the fourth is auto‑created.
- Both the numeric ID and the textual name are exposed via the API and token payload.
- Frontend mapping constants are kept in sync via `constants.py`.
- Permissions checks rely on the numeric `id_rol`.

Feel free to ask if you need more details on a specific endpoint, permission class, or how to extend the role set.

