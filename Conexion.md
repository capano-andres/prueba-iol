# 📌 Guía de Uso y Documentación: Autenticación IOL

## ¿Cómo utilizar este archivo?
1. Copia todo este texto.
2. Abre tu editor de código o texto favorito (Visual Studio Code, Obsidian, Bloc de notas, TextEdit, etc.).
3. Pega el contenido y guarda el archivo con el nombre **`Autenticacion_IOL.md`** (es crucial que la extensión sea `.md`).
4. Ábrelo en cualquier visor compatible con Markdown (como GitHub o los editores mencionados) para ver las tablas, el código y los formatos aplicados correctamente.

---

# 🔐 Autenticación - API InvertirOnline (IOL)

La API de InvertirOnline utiliza el estándar **OAuth 2.0** para la autenticación y autorización. Para consumir los servicios y endpoints de la API, es necesario obtener un `access_token` (Token de Acceso) que te identificará de forma segura.

## 1. Obtención del Token de Acceso

Para obtener el token inicial, debes realizar una petición HTTP POST al endpoint de generación de tokens utilizando tus credenciales (las mismas que usas para ingresar a tu cuenta de IOL).

* **Endpoint:** `POST https://api.invertironline.com/token`
* **Content-Type:** `application/x-www-form-urlencoded`

### Parámetros del Body

| Parámetro | Tipo | Descripción |
| :--- | :--- | :--- |
| `username` | `string` | Tu nombre de usuario de InvertirOnline. |
| `password` | `string` | Tu contraseña de InvertirOnline. |
| `grant_type` | `string` | Debe tener el valor exacto: `password`. |

### Ejemplo de Petición (cURL)

```bash
curl -X POST "[https://api.invertironline.com/token](https://api.invertironline.com/token)" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "username=MI_USUARIO&password=MI_PASSWORD&grant_type=password"