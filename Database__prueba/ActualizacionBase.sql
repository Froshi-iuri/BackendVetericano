-- ============================================================
-- ESQUEMA CORREGIDO — verificado con imagen en alta resolución
-- Regla aplicada: flecha A -> B significa "B tiene FK hacia A"
-- ============================================================

-- ---------- Catálogos base ----------
CREATE TABLE especies (
    id_especie SERIAL PRIMARY KEY,
    nombre     VARCHAR(100) NOT NULL
);

CREATE TABLE responsables (
    id_responsable SERIAL PRIMARY KEY,
    nombre          VARCHAR(150) NOT NULL,
    documento       VARCHAR(30),
    telefono        VARCHAR(30),
    direccion       VARCHAR(255)
);

CREATE TABLE rol (
    id_rol     SERIAL PRIMARY KEY,
    nombre_rol VARCHAR(50) NOT NULL
);

CREATE TABLE tipo_peticion (
    id_tipo SERIAL PRIMARY KEY,
    nombre  VARCHAR(100) NOT NULL
);

CREATE TABLE ubicaciones (
    id_ubicacion SERIAL PRIMARY KEY,
    direccion    VARCHAR(255),
    barrio       VARCHAR(100),
    ciudad       VARCHAR(100)
);

CREATE TABLE estado_peticiones (
    id_estado SERIAL PRIMARY KEY,
    nombre    VARCHAR(100) NOT NULL
);

CREATE TABLE tipos_eventos (
    id_tipo_evento SERIAL PRIMARY KEY,
    nombre         VARCHAR(100) NOT NULL
);

CREATE TABLE proveedores (
    id_proveedor SERIAL PRIMARY KEY,
    nombre       VARCHAR(150) NOT NULL,
    telefono     VARCHAR(30)
);

CREATE TABLE patologia (
    id_patologia SERIAL PRIMARY KEY,
    nombre       VARCHAR(150) NOT NULL
);

-- ---------- Módulo animal ----------
CREATE TABLE raza (
    id_raza    SERIAL PRIMARY KEY,
    id_especie INT NOT NULL,
    nombre     VARCHAR(100) NOT NULL,
    CONSTRAINT fk_raza_especie FOREIGN KEY (id_especie) REFERENCES especies (id_especie)
);

CREATE TABLE animal (
    id_animal SERIAL PRIMARY KEY,
    id_raza   INT NOT NULL,
    nombre    VARCHAR(100) NOT NULL,
    CONSTRAINT fk_animal_raza FOREIGN KEY (id_raza) REFERENCES raza (id_raza)
);

-- CORREGIDO: estados_animal es un HISTORIAL por animal, no un catálogo
-- estático. Cada fila registra un cambio de estado de un animal específico.
CREATE TABLE estados_animal (
    id_estado_animal SERIAL PRIMARY KEY,
    id_animal        INT NOT NULL,
    nombre_estado    VARCHAR(100) NOT NULL,
    fecha_registro   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    observacion      TEXT,
    CONSTRAINT fk_ea_animal FOREIGN KEY (id_animal) REFERENCES animal (id_animal)
);

CREATE TABLE animal_responsable (
    id_animal_responsable SERIAL PRIMARY KEY,
    id_animal             INT NOT NULL,
    id_responsable        INT NOT NULL,
    CONSTRAINT fk_ar_animal      FOREIGN KEY (id_animal)      REFERENCES animal (id_animal),
    CONSTRAINT fk_ar_responsable FOREIGN KEY (id_responsable) REFERENCES responsables (id_responsable)
);

CREATE TABLE usuarios (
    id_usuario SERIAL PRIMARY KEY,
    id_rol     INT NOT NULL,
    nombre     VARCHAR(150) NOT NULL,
    email      VARCHAR(150) NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,
    CONSTRAINT fk_usuario_rol FOREIGN KEY (id_rol) REFERENCES rol (id_rol)
);

-- ---------- Módulo historia clínica ----------
CREATE TABLE historia_clinica (
    id_historia    SERIAL PRIMARY KEY,
    id_animal      INT NOT NULL UNIQUE,
    fecha_apertura TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_hc_animal FOREIGN KEY (id_animal) REFERENCES animal (id_animal)
);

CREATE TABLE consulta (
    id_consulta SERIAL PRIMARY KEY,
    id_historia INT NOT NULL,
    fecha_hora  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    prioridad   VARCHAR(20),
    CONSTRAINT fk_consulta_historia FOREIGN KEY (id_historia) REFERENCES historia_clinica (id_historia)
);

-- CONFIRMADO: una sola dirección, sin referencia circular
CREATE TABLE hospitalizacion_seres_sintientes (
    id_hospitalizacion SERIAL PRIMARY KEY,
    id_consulta        INT NOT NULL,
    descripcion_estado TEXT,
    fecha_ingreso      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_hss_consulta FOREIGN KEY (id_consulta) REFERENCES consulta (id_consulta)
);

CREATE TABLE tratamientos (
    id_tratamiento SERIAL PRIMARY KEY,
    id_consulta    INT NOT NULL,
    descripcion    VARCHAR(255),
    CONSTRAINT fk_trat_consulta FOREIGN KEY (id_consulta) REFERENCES consulta (id_consulta)
);

CREATE TABLE diagnostico (
    id_diagnostico SERIAL PRIMARY KEY,
    id_consulta    INT NOT NULL,
    descripcion    TEXT,
    CONSTRAINT fk_diag_consulta FOREIGN KEY (id_consulta) REFERENCES consulta (id_consulta)
);

CREATE TABLE examen (
    id_examen   SERIAL PRIMARY KEY,
    id_consulta INT NOT NULL,
    resultado   TEXT,
    CONSTRAINT fk_examen_consulta FOREIGN KEY (id_consulta) REFERENCES consulta (id_consulta)
);

CREATE TABLE seguimientos_clinicos (
    id_seguimiento SERIAL PRIMARY KEY,
    id_consulta    INT NOT NULL,
    evolucion      TEXT,
    fecha          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_sc_consulta FOREIGN KEY (id_consulta) REFERENCES consulta (id_consulta)
);

CREATE TABLE diagnostico_patologia (
    id_diagnostico_patologia SERIAL PRIMARY KEY,
    id_diagnostico            INT NOT NULL,
    id_patologia               INT NOT NULL,
    CONSTRAINT fk_dp_diagnostico FOREIGN KEY (id_diagnostico) REFERENCES diagnostico (id_diagnostico),
    CONSTRAINT fk_dp_patologia   FOREIGN KEY (id_patologia)   REFERENCES patologia (id_patologia)
);

CREATE TABLE procedimiento_realizado (
    id_procedimiento   SERIAL PRIMARY KEY,
    id_hospitalizacion INT NOT NULL,
    descripcion        TEXT,
    CONSTRAINT fk_pr_hospitalizacion FOREIGN KEY (id_hospitalizacion) REFERENCES hospitalizacion_seres_sintientes (id_hospitalizacion)
);

CREATE TABLE seguimiento_hospitalario (
    id_seguimiento_hosp SERIAL PRIMARY KEY,
    id_hospitalizacion  INT NOT NULL,
    evolucion           TEXT,
    fecha               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_sh_hospitalizacion FOREIGN KEY (id_hospitalizacion) REFERENCES hospitalizacion_seres_sintientes (id_hospitalizacion)
);

-- ---------- Módulo medicamentos / compras ----------
CREATE TABLE compra (
    id_compra    SERIAL PRIMARY KEY,
    id_proveedor INT NOT NULL,
    fecha        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_compra_proveedor FOREIGN KEY (id_proveedor) REFERENCES proveedores (id_proveedor)
);

CREATE TABLE salidas (
    id_salida SERIAL PRIMARY KEY,
    fecha     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    motivo    VARCHAR(100)
);

-- ⚠️ NOTA DE DISEÑO (confirmado visualmente, no es error de lectura):
-- "medicamentos" tiene FK hacia "salidas". Esto significa que cada fila
-- de medicamentos queda ligada a UNA sola salida — no podrás repetir el
-- mismo medicamento en múltiples salidas futuras sin crear una fila
-- nueva por cada una. Si la intención real es "un medicamento puede
-- tener muchas salidas a lo largo del tiempo", el diseño estándar sería
-- invertirlo (salidas.id_medicamento). Coméntenlo con su equipo/docente
-- para confirmar si es intencional.
CREATE TABLE medicamentos (
    id_medicamento SERIAL PRIMARY KEY,
    id_salida      INT,
    nombre         VARCHAR(150) NOT NULL,
    CONSTRAINT fk_med_salida FOREIGN KEY (id_salida) REFERENCES salidas (id_salida)
);

-- detalle_compra <- compra, <- medicamentos
CREATE TABLE detalle_compra (
    id_detalle_compra SERIAL PRIMARY KEY,
    id_compra         INT NOT NULL,
    id_medicamento    INT NOT NULL,
    cantidad          INT,
    precio_unitario   NUMERIC(10,2),
    CONSTRAINT fk_dc_compra      FOREIGN KEY (id_compra)      REFERENCES compra (id_compra),
    CONSTRAINT fk_dc_medicamento FOREIGN KEY (id_medicamento) REFERENCES medicamentos (id_medicamento)
);

-- ADMINISTRACION_medicamento <- medicamentos, <- seguimiento_hospitalario
CREATE TABLE administracion_medicamento (
    id_administracion   SERIAL PRIMARY KEY,
    id_medicamento      INT NOT NULL,
    id_seguimiento_hosp INT NOT NULL,
    dosis_administrada  VARCHAR(100),
    fecha_hora          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_am_medicamento FOREIGN KEY (id_medicamento)     REFERENCES medicamentos (id_medicamento),
    CONSTRAINT fk_am_seguimiento FOREIGN KEY (id_seguimiento_hosp) REFERENCES seguimiento_hospitalario (id_seguimiento_hosp)
);

-- detalle_salida <- usuarios, <- administracion_medicamento
CREATE TABLE detalle_salida (
    id_detalle_salida SERIAL PRIMARY KEY,
    id_usuario        INT NOT NULL,
    id_administracion INT NOT NULL,
    cantidad          INT,
    CONSTRAINT fk_ds_usuario        FOREIGN KEY (id_usuario)        REFERENCES usuarios (id_usuario),
    CONSTRAINT fk_ds_administracion FOREIGN KEY (id_administracion) REFERENCES administracion_medicamento (id_administracion)
);

-- inventarios <- detalle_salida, <- detalle_compra
CREATE TABLE inventarios (
    id_inventario     SERIAL PRIMARY KEY,
    id_detalle_salida INT,
    id_detalle_compra INT,
    cantidad_actual   INT NOT NULL DEFAULT 0,
    CONSTRAINT fk_inv_detalle_salida FOREIGN KEY (id_detalle_salida) REFERENCES detalle_salida (id_detalle_salida),
    CONSTRAINT fk_inv_detalle_compra FOREIGN KEY (id_detalle_compra) REFERENCES detalle_compra (id_detalle_compra)
);

CREATE TABLE tratamiento_medicamentos (
    id_tratamiento_medicamento SERIAL PRIMARY KEY,
    id_tratamiento              INT NOT NULL,
    id_medicamento               INT NOT NULL,
    dosis                          VARCHAR(100),
    CONSTRAINT fk_tm_tratamiento FOREIGN KEY (id_tratamiento) REFERENCES tratamientos (id_tratamiento),
    CONSTRAINT fk_tm_medicamento FOREIGN KEY (id_medicamento) REFERENCES medicamentos (id_medicamento)
);

-- ---------- Módulo peticiones ----------
-- CORREGIDO: peticiones YA NO tiene FK directa a animal (esa conexión
-- se confirmó que en realidad no existe en el diagrama). El vínculo con
-- el animal ocurre más adelante, en seguimiento_peticiones_visita.
CREATE TABLE peticiones (
    id_peticion    SERIAL PRIMARY KEY,
    id_tipo        INT NOT NULL,
    id_ubicacion   INT,
    id_estado      INT NOT NULL,
    responsable_id INT NOT NULL,
    descripcion    TEXT,
    fecha          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_pet_tipo        FOREIGN KEY (id_tipo)      REFERENCES tipo_peticion (id_tipo),
    CONSTRAINT fk_pet_ubicacion   FOREIGN KEY (id_ubicacion) REFERENCES ubicaciones (id_ubicacion),
    CONSTRAINT fk_pet_estado      FOREIGN KEY (id_estado)    REFERENCES estado_peticiones (id_estado),
    CONSTRAINT fk_pet_responsable FOREIGN KEY (responsable_id) REFERENCES usuarios (id_usuario)
);

CREATE TABLE evidencia_peticiones (
    id_evidencia SERIAL PRIMARY KEY,
    id_peticion  INT NOT NULL,
    ruta_archivo VARCHAR(500),
    CONSTRAINT fk_ep_peticion FOREIGN KEY (id_peticion) REFERENCES peticiones (id_peticion)
);

-- seguimiento_peticiones_visita <- peticiones, <- usuarios (veterinario),
-- y referencia directa a animal (tal como aparece anotado en el diagrama)
CREATE TABLE seguimiento_peticiones_visita (
    id_seguimiento SERIAL PRIMARY KEY,
    id_peticion    INT NOT NULL,
    id_veterinario INT NOT NULL,
    id_animal      INT,
    observacion    TEXT,
    fecha          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_spv_peticion    FOREIGN KEY (id_peticion)    REFERENCES peticiones (id_peticion),
    CONSTRAINT fk_spv_veterinario FOREIGN KEY (id_veterinario) REFERENCES usuarios (id_usuario),
    CONSTRAINT fk_spv_animal      FOREIGN KEY (id_animal)      REFERENCES animal (id_animal)
);

-- visita_animal <- animal, <- seguimiento_peticiones_visita
CREATE TABLE visita_animal (
    id_visita      SERIAL PRIMARY KEY,
    id_animal      INT NOT NULL,
    id_seguimiento INT,
    fecha          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_va_animal      FOREIGN KEY (id_animal)      REFERENCES animal (id_animal),
    CONSTRAINT fk_va_seguimiento FOREIGN KEY (id_seguimiento) REFERENCES seguimiento_peticiones_visita (id_seguimiento)
);

-- ---------- Módulo voluntariado / eventos ----------
CREATE TABLE eventos (
    id_evento      SERIAL PRIMARY KEY,
    id_tipo_evento INT NOT NULL,
    nombre         VARCHAR(150),
    fecha          TIMESTAMP,
    CONSTRAINT fk_evento_tipo FOREIGN KEY (id_tipo_evento) REFERENCES tipos_eventos (id_tipo_evento)
);

-- CORREGIDO: voluntario_evento SÍ tiene FK directa a animal (confirmado
-- con la línea larga que rodea todo el diagrama), además de usuario y evento.
CREATE TABLE voluntario_evento (
    id_voluntario_evento SERIAL PRIMARY KEY,
    id_usuario           INT NOT NULL,
    id_evento            INT NOT NULL,
    id_animal            INT,
    fecha                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ve_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios (id_usuario),
    CONSTRAINT fk_ve_evento  FOREIGN KEY (id_evento)  REFERENCES eventos (id_evento),
    CONSTRAINT fk_ve_animal  FOREIGN KEY (id_animal)  REFERENCES animal (id_animal)
);