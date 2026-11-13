-- Estructura schema-only de public (sin datos reales ni credenciales)
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
--
-- PostgreSQL database dump
--


-- Dumped from database version 17.6
-- Dumped by pg_dump version 18.7

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: fn_actualizar_inventario_entrada(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_actualizar_inventario_entrada() RETURNS trigger
    LANGUAGE plpgsql
    AS $$

BEGIN

    UPDATE inventario

    SET cantidad_disponible = cantidad_disponible + NEW.cantidad

    WHERE id_medicamento = NEW.id_medicamento;

    RETURN NEW;

END;

$$;


--
-- Name: fn_actualizar_inventario_salida(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_actualizar_inventario_salida() RETURNS trigger
    LANGUAGE plpgsql
    AS $$

BEGIN

    UPDATE inventario

    SET cantidad_disponible = cantidad_disponible - NEW.cantidad

    WHERE id_medicamento = NEW.id_medicamento;

    RETURN NEW;

END;

$$;


--
-- Name: fn_alerta_adopcion_disponible(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_alerta_adopcion_disponible() RETURNS trigger
    LANGUAGE plpgsql
    AS $$

DECLARE

    nombre_estado VARCHAR(100);

BEGIN

    IF NEW.id_estado IS DISTINCT FROM OLD.id_estado THEN

        SELECT nombre INTO nombre_estado FROM estados_animal WHERE id_estado = NEW.id_estado;

        IF nombre_estado = 'Disponible para adopcion' THEN

            INSERT INTO alertas (tipo_alerta, entidad_tipo, entidad_id, mensaje)

            VALUES ('adopcion_disponible', 'animal', NEW.id_animal,

                    'El animal ' || NEW.nombre || ' ya está listo para adopción.');

        END IF;

    END IF;

    RETURN NEW;

END;

$$;


--
-- Name: fn_alerta_stock_bajo(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_alerta_stock_bajo() RETURNS trigger
    LANGUAGE plpgsql
    AS $$

BEGIN

    IF NEW.cantidad_disponible < NEW.stock_minimo THEN

        INSERT INTO alertas (tipo_alerta, entidad_tipo, entidad_id, mensaje)

        VALUES ('stock_bajo', 'medicamento', NEW.id_medicamento,

                'El medicamento ID ' || NEW.id_medicamento || ' está por debajo del stock mínimo.');

    END IF;

    RETURN NEW;

END;

$$;


--
-- Name: fn_proteger_inmutabilidad_kardex(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_proteger_inmutabilidad_kardex() RETURNS trigger
    LANGUAGE plpgsql
    AS $$

BEGIN

    RAISE EXCEPTION 'Operación denegada en tabla inventario_kardex: El kardex es inmutable. No se permite UPDATE ni DELETE. Cualquier corrección debe realizarse mediante un movimiento compensatorio de tipo AJUSTE_REVERSION.';

END;

$$;


--
-- Name: fn_proteger_truncate_kardex(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_proteger_truncate_kardex() RETURNS trigger
    LANGUAGE plpgsql
    AS $$

BEGIN

    RAISE EXCEPTION 'Operación denegada en tabla inventario_kardex: El kardex es inmutable. Prohibido ejecutar TRUNCATE.';

END;

$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: administracion_medicamento; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.administracion_medicamento (
    id_administracion integer NOT NULL,
    id_medicamento integer NOT NULL,
    id_seguimiento_hosp integer NOT NULL,
    dosis_administrada character varying(100),
    fecha_hora timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: administracion_medicamento_id_administracion_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.administracion_medicamento_id_administracion_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: administracion_medicamento_id_administracion_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.administracion_medicamento_id_administracion_seq OWNED BY public.administracion_medicamento.id_administracion;


--
-- Name: adopciones_solicitudadopcion; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adopciones_solicitudadopcion (
    id bigint NOT NULL,
    nombre_adoptante character varying(150) NOT NULL,
    cedula character varying(20) NOT NULL,
    correo_adoptante character varying(254) NOT NULL,
    telefono character varying(20) NOT NULL,
    mensaje text,
    fecha_solicitud timestamp with time zone NOT NULL,
    animal_id integer NOT NULL
);


--
-- Name: adopciones_solicitudadopcion_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.adopciones_solicitudadopcion ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.adopciones_solicitudadopcion_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: animal; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.animal (
    id_animal integer NOT NULL,
    id_raza integer NOT NULL,
    nombre character varying(100) NOT NULL,
    sexo character varying(10),
    fecha_ingreso date,
    caracteristicas text,
    observaciones text,
    foto_url character varying(255),
    activo boolean DEFAULT true,
    color character varying(50),
    fecha_nacimiento date,
    peso numeric(5,2),
    esterilizado boolean DEFAULT false,
    disponible_adopcion boolean DEFAULT false
);


--
-- Name: animal_id_animal_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.animal_id_animal_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: animal_id_animal_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.animal_id_animal_seq OWNED BY public.animal.id_animal;


--
-- Name: animal_responsable; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.animal_responsable (
    id_animal_responsable integer NOT NULL,
    id_animal integer NOT NULL,
    id_responsable integer NOT NULL,
    tipo_responsabilidad character varying(50),
    fecha_inicio date,
    fecha_fin date,
    observaciones text
);


--
-- Name: animal_responsable_id_animal_responsable_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.animal_responsable_id_animal_responsable_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: animal_responsable_id_animal_responsable_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.animal_responsable_id_animal_responsable_seq OWNED BY public.animal_responsable.id_animal_responsable;


--
-- Name: auth_group; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_group (
    id integer NOT NULL,
    name character varying(150) NOT NULL
);


--
-- Name: auth_group_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.auth_group ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.auth_group_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: auth_group_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_group_permissions (
    id bigint NOT NULL,
    group_id integer NOT NULL,
    permission_id integer NOT NULL
);


--
-- Name: auth_group_permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.auth_group_permissions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.auth_group_permissions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: auth_permission; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_permission (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    content_type_id integer NOT NULL,
    codename character varying(100) NOT NULL
);


--
-- Name: auth_permission_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.auth_permission ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.auth_permission_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: auth_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_user (
    id integer NOT NULL,
    password character varying(128) NOT NULL,
    last_login timestamp with time zone,
    is_superuser boolean NOT NULL,
    username character varying(150) NOT NULL,
    first_name character varying(150) NOT NULL,
    last_name character varying(150) NOT NULL,
    email character varying(254) NOT NULL,
    is_staff boolean NOT NULL,
    is_active boolean NOT NULL,
    date_joined timestamp with time zone NOT NULL
);


--
-- Name: auth_user_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_user_groups (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    group_id integer NOT NULL
);


--
-- Name: auth_user_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.auth_user_groups ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.auth_user_groups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: auth_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.auth_user ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.auth_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: auth_user_user_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_user_user_permissions (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    permission_id integer NOT NULL
);


--
-- Name: auth_user_user_permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.auth_user_user_permissions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.auth_user_user_permissions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: authtoken_token; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.authtoken_token (
    key character varying(40) NOT NULL,
    created timestamp with time zone NOT NULL,
    user_id integer NOT NULL
);


--
-- Name: compra; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.compra (
    id_compra integer NOT NULL,
    id_proveedor integer NOT NULL,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: compra_id_compra_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.compra_id_compra_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: compra_id_compra_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.compra_id_compra_seq OWNED BY public.compra.id_compra;


--
-- Name: consulta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.consulta (
    id_consulta integer NOT NULL,
    id_historia integer NOT NULL,
    fecha_hora timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    prioridad character varying(20),
    motivo_consulta character varying(255),
    anamnesis text,
    hallazgos_examen text,
    resultado_estado character varying(100),
    frecuencia_respiratoria integer,
    frecuencia_cardiaca integer,
    temperatura numeric(4,1),
    pulso character varying(50),
    tllc character varying(50),
    ganglios_linfaticos character varying(100),
    mucosas character varying(100),
    actitud_temperamento character varying(50),
    evaluacion_sistemas jsonb,
    observaciones_examen text,
    materiales_utilizados text,
    ingresa_cba boolean DEFAULT false,
    examenes_complementarios jsonb,
    pronostico character varying(50),
    plan_procedimientos text,
    motivo_ingreso_cba text,
    canil_asignado character varying(50),
    funcionario_recibe_cba character varying(150)
);


--
-- Name: consulta_id_consulta_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.consulta_id_consulta_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: consulta_id_consulta_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.consulta_id_consulta_seq OWNED BY public.consulta.id_consulta;


--
-- Name: detalle_compra; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.detalle_compra (
    id_detalle_compra integer NOT NULL,
    id_compra integer NOT NULL,
    id_medicamento integer NOT NULL,
    cantidad integer
);


--
-- Name: detalle_compra_id_detalle_compra_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.detalle_compra_id_detalle_compra_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: detalle_compra_id_detalle_compra_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.detalle_compra_id_detalle_compra_seq OWNED BY public.detalle_compra.id_detalle_compra;


--
-- Name: detalle_salida; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.detalle_salida (
    id_detalle_salida integer NOT NULL,
    id_usuario integer NOT NULL,
    id_administracion integer NOT NULL,
    cantidad integer
);


--
-- Name: detalle_salida_id_detalle_salida_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.detalle_salida_id_detalle_salida_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: detalle_salida_id_detalle_salida_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.detalle_salida_id_detalle_salida_seq OWNED BY public.detalle_salida.id_detalle_salida;


--
-- Name: diagnostico; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.diagnostico (
    id_diagnostico integer NOT NULL,
    id_consulta integer NOT NULL,
    descripcion text,
    tipo_diagnostico character varying(50),
    lista_problemas text
);


--
-- Name: diagnostico_id_diagnostico_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.diagnostico_id_diagnostico_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diagnostico_id_diagnostico_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.diagnostico_id_diagnostico_seq OWNED BY public.diagnostico.id_diagnostico;


--
-- Name: diagnostico_patologia; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.diagnostico_patologia (
    id_diagnostico_patologia integer NOT NULL,
    id_diagnostico integer NOT NULL,
    id_patologia integer NOT NULL,
    hallazgos_pruebas text
);


--
-- Name: diagnostico_patologia_id_diagnostico_patologia_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.diagnostico_patologia_id_diagnostico_patologia_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diagnostico_patologia_id_diagnostico_patologia_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.diagnostico_patologia_id_diagnostico_patologia_seq OWNED BY public.diagnostico_patologia.id_diagnostico_patologia;


--
-- Name: django_admin_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.django_admin_log (
    id integer NOT NULL,
    action_time timestamp with time zone NOT NULL,
    object_id text,
    object_repr character varying(200) NOT NULL,
    action_flag smallint NOT NULL,
    change_message text NOT NULL,
    content_type_id integer,
    user_id integer NOT NULL,
    CONSTRAINT django_admin_log_action_flag_check CHECK ((action_flag >= 0))
);


--
-- Name: django_admin_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.django_admin_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.django_admin_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: django_content_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.django_content_type (
    id integer NOT NULL,
    app_label character varying(100) NOT NULL,
    model character varying(100) NOT NULL
);


--
-- Name: django_content_type_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.django_content_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.django_content_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: django_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.django_migrations (
    id bigint NOT NULL,
    app character varying(255) NOT NULL,
    name character varying(255) NOT NULL,
    applied timestamp with time zone NOT NULL
);


--
-- Name: django_migrations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.django_migrations ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.django_migrations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: django_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.django_session (
    session_key character varying(40) NOT NULL,
    session_data text NOT NULL,
    expire_date timestamp with time zone NOT NULL
);


--
-- Name: especies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.especies (
    id_especie integer NOT NULL,
    nombre character varying(100) NOT NULL,
    descripcion text,
    activo boolean DEFAULT true NOT NULL
);


--
-- Name: especie_id_especie_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.especie_id_especie_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: especie_id_especie_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.especie_id_especie_seq OWNED BY public.especies.id_especie;


--
-- Name: estado_peticiones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.estado_peticiones (
    id_estado integer NOT NULL,
    nombre character varying(100) NOT NULL,
    descripcion text,
    orden integer,
    activo boolean DEFAULT true
);


--
-- Name: estado_peticiones_id_estado_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.estado_peticiones_id_estado_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: estado_peticiones_id_estado_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.estado_peticiones_id_estado_seq OWNED BY public.estado_peticiones.id_estado;


--
-- Name: estados_animal; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.estados_animal (
    id_estado_animal integer NOT NULL,
    id_animal integer NOT NULL,
    nombre_estado character varying(100) NOT NULL,
    fecha_registro timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    observacion text
);


--
-- Name: estados_animal_id_estado_animal_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.estados_animal_id_estado_animal_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: estados_animal_id_estado_animal_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.estados_animal_id_estado_animal_seq OWNED BY public.estados_animal.id_estado_animal;


--
-- Name: eventos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eventos (
    id_evento integer NOT NULL,
    id_tipo_evento integer NOT NULL,
    nombre character varying(150),
    fecha timestamp without time zone,
    descripcion character varying(500)
);


--
-- Name: eventos_id_evento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.eventos_id_evento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: eventos_id_evento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.eventos_id_evento_seq OWNED BY public.eventos.id_evento;


--
-- Name: evidencia_peticiones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidencia_peticiones (
    id_evidencia integer NOT NULL,
    id_peticion integer NOT NULL,
    ruta_archivo character varying(500),
    descripcion character varying(255)
);


--
-- Name: evidencia_peticiones_id_evidencia_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.evidencia_peticiones_id_evidencia_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evidencia_peticiones_id_evidencia_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.evidencia_peticiones_id_evidencia_seq OWNED BY public.evidencia_peticiones.id_evidencia;


--
-- Name: evidencia_seguimiento_visita; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidencia_seguimiento_visita (
    id_evidencia integer NOT NULL,
    id_seguimiento integer NOT NULL,
    ruta_archivo character varying(500) NOT NULL,
    tipo_archivo character varying(20) DEFAULT 'imagen'::character varying,
    descripcion character varying(255),
    fecha timestamp without time zone DEFAULT now()
);


--
-- Name: evidencia_seguimiento_visita_id_evidencia_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.evidencia_seguimiento_visita_id_evidencia_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evidencia_seguimiento_visita_id_evidencia_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.evidencia_seguimiento_visita_id_evidencia_seq OWNED BY public.evidencia_seguimiento_visita.id_evidencia;


--
-- Name: examen; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.examen (
    id_examen integer NOT NULL,
    id_consulta integer,
    resultado text,
    nombre_tipo_examen character varying(100),
    solicitado boolean DEFAULT true,
    descripcion_hallazgos text,
    ruta_archivo_resultado character varying(500),
    fecha_realizacion timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    estado text DEFAULT 'activo'::text
);


--
-- Name: examen_id_examen_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.examen_id_examen_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: examen_id_examen_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.examen_id_examen_seq OWNED BY public.examen.id_examen;


--
-- Name: historia_clinica; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.historia_clinica (
    id_historia integer NOT NULL,
    id_animal integer NOT NULL,
    fecha_apertura timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    estado_general character varying(50),
    numero_historia character varying(50),
    fecha_ultima_desparasitacion date,
    producto_desparasitacion character varying(150),
    vacunas text,
    enfermedades_anteriores text,
    tratamientos_anteriores text,
    evolucion_previa text,
    alimentacion character varying(255),
    estado_reproductivo character varying(30),
    fecha_ultimo_celo date,
    fecha_ultimo_parto date
);


--
-- Name: historia_clinica_id_historia_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.historia_clinica_id_historia_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: historia_clinica_id_historia_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.historia_clinica_id_historia_seq OWNED BY public.historia_clinica.id_historia;


--
-- Name: historial_peticiones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.historial_peticiones (
    id_historial integer NOT NULL,
    id_peticion integer NOT NULL,
    tipo_cambio character varying(30) NOT NULL,
    id_estado_anterior integer,
    id_estado_nuevo integer,
    id_veterinario_anterior integer,
    id_veterinario_nuevo integer,
    id_usuario_accion integer,
    observacion text,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: historial_peticiones_id_historial_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.historial_peticiones_id_historial_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: historial_peticiones_id_historial_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.historial_peticiones_id_historial_seq OWNED BY public.historial_peticiones.id_historial;


--
-- Name: hospitalizacion_seres_sintientes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hospitalizacion_seres_sintientes (
    id_hospitalizacion integer NOT NULL,
    id_consulta integer NOT NULL,
    descripcion_estado text,
    fecha_ingreso timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    diagnostico_presuntivo text,
    responsable_clinico character varying(150),
    hoja_numero integer
);


--
-- Name: hospitalizacion_seres_sintientes_id_hospitalizacion_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hospitalizacion_seres_sintientes_id_hospitalizacion_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hospitalizacion_seres_sintientes_id_hospitalizacion_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hospitalizacion_seres_sintientes_id_hospitalizacion_seq OWNED BY public.hospitalizacion_seres_sintientes.id_hospitalizacion;


--
-- Name: inventario_consumos_registro; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventario_consumos_registro (
    id_consumo bigint NOT NULL,
    client_uuid uuid NOT NULL,
    id_veterinario integer NOT NULL,
    id_medicamento integer NOT NULL,
    cantidad_solicitada integer NOT NULL,
    cantidad_descontada integer DEFAULT 0 NOT NULL,
    estado character varying(25) NOT NULL,
    codigo_http integer DEFAULT 200 NOT NULL,
    origen_tipo character varying(30) NOT NULL,
    id_consulta integer,
    id_tratamiento integer,
    id_seguimiento_visita integer,
    id_visita_animal integer,
    motivo_rechazo text,
    stock_disponible_al_rechazo integer,
    fecha_consumo timestamp with time zone DEFAULT now() NOT NULL,
    fecha_resolucion_conflicto timestamp with time zone,
    resuelto_por integer,
    CONSTRAINT chk_consumo_cant_desc_no_neg CHECK ((cantidad_descontada >= 0)),
    CONSTRAINT chk_consumo_cant_sol_pos CHECK ((cantidad_solicitada > 0)),
    CONSTRAINT chk_consumo_estado CHECK (((estado)::text = ANY ((ARRAY['APLICADO'::character varying, 'RECHAZADO_CONFLICTO'::character varying, 'REVERTIDO'::character varying])::text[]))),
    CONSTRAINT chk_consumo_origen CHECK (((origen_tipo)::text = ANY ((ARRAY['HISTORIA_CLINICA'::character varying, 'ACTA_VISITA'::character varying, 'DIRECTO'::character varying])::text[])))
);


--
-- Name: inventario_consumos_registro_id_consumo_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.inventario_consumos_registro_id_consumo_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inventario_consumos_registro_id_consumo_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.inventario_consumos_registro_id_consumo_seq OWNED BY public.inventario_consumos_registro.id_consumo;


--
-- Name: inventario_kardex; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventario_kardex (
    id_kardex bigint NOT NULL,
    tipo_movimiento character varying(30) NOT NULL,
    ubicacion character varying(20) NOT NULL,
    id_usuario_ejecuta integer NOT NULL,
    id_veterinario integer,
    id_medicamento integer NOT NULL,
    id_inventario integer NOT NULL,
    cantidad integer NOT NULL,
    saldo_anterior integer NOT NULL,
    saldo_resultante integer NOT NULL,
    motivo text NOT NULL,
    id_consulta integer,
    id_tratamiento integer,
    id_seguimiento_visita integer,
    id_visita_animal integer,
    id_solicitud_kit bigint,
    id_kardex_revertido bigint,
    client_uuid uuid,
    fecha_hora timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_kardex_cantidad_pos CHECK ((cantidad > 0)),
    CONSTRAINT chk_kardex_saldo_ant_no_neg CHECK ((saldo_anterior >= 0)),
    CONSTRAINT chk_kardex_saldo_res_no_neg CHECK ((saldo_resultante >= 0)),
    CONSTRAINT chk_kardex_tipo CHECK (((tipo_movimiento)::text = ANY ((ARRAY['ASIGNACION_KIT'::character varying, 'CONSUMO_HISTORIA'::character varying, 'CONSUMO_ACTA'::character varying, 'DEVOLUCION_APROBADA'::character varying, 'RETIRO_GESTOR'::character varying, 'BAJA_VENCIMIENTO'::character varying, 'AJUSTE_REVERSION'::character varying, 'ENTRADA_COMPRA'::character varying])::text[]))),
    CONSTRAINT chk_kardex_ubicacion CHECK (((ubicacion)::text = ANY ((ARRAY['GENERAL'::character varying, 'KIT_VETERINARIO'::character varying])::text[])))
);


--
-- Name: inventario_kardex_id_kardex_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.inventario_kardex_id_kardex_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inventario_kardex_id_kardex_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.inventario_kardex_id_kardex_seq OWNED BY public.inventario_kardex.id_kardex;


--
-- Name: inventarios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventarios (
    id_inventario integer NOT NULL,
    id_detalle_salida integer,
    id_detalle_compra integer,
    cantidad_actual integer DEFAULT 0 NOT NULL,
    fecha_vencimiento date,
    estado character varying(50)
);


--
-- Name: inventarios_id_inventario_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.inventarios_id_inventario_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inventarios_id_inventario_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.inventarios_id_inventario_seq OWNED BY public.inventarios.id_inventario;


--
-- Name: kit_veterinario_lotes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kit_veterinario_lotes (
    id_kit_lote bigint NOT NULL,
    id_veterinario integer NOT NULL,
    id_inventario integer NOT NULL,
    id_medicamento integer NOT NULL,
    cantidad_actual integer DEFAULT 0 NOT NULL,
    cantidad_referencia integer DEFAULT 0 NOT NULL,
    fecha_vencimiento date,
    fecha_compra_origen timestamp with time zone,
    creado_el timestamp with time zone DEFAULT now() NOT NULL,
    actualizado_el timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_kit_cantidad_actual_no_negativa CHECK ((cantidad_actual >= 0)),
    CONSTRAINT chk_kit_cantidad_ref_no_negativa CHECK ((cantidad_referencia >= 0))
);


--
-- Name: kit_veterinario_lotes_id_kit_lote_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kit_veterinario_lotes_id_kit_lote_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kit_veterinario_lotes_id_kit_lote_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kit_veterinario_lotes_id_kit_lote_seq OWNED BY public.kit_veterinario_lotes.id_kit_lote;


--
-- Name: medicamentos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.medicamentos (
    id_medicamento integer NOT NULL,
    id_salida integer,
    nombre character varying(150) NOT NULL,
    principio_activo character varying(150),
    presentacion character varying(100),
    concentracion character varying(50),
    descripcion text,
    activo boolean DEFAULT true,
    cantidad_ml character varying(50),
    tipo character varying(50)
);


--
-- Name: medicamentos_id_medicamento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.medicamentos_id_medicamento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: medicamentos_id_medicamento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.medicamentos_id_medicamento_seq OWNED BY public.medicamentos.id_medicamento;


--
-- Name: medicamentos_medicamento; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.medicamentos_medicamento (
    id bigint NOT NULL,
    nombre character varying(150) NOT NULL,
    descripcion text,
    cantidad_ml character varying(50) NOT NULL,
    tipo character varying(100) NOT NULL,
    estado boolean NOT NULL,
    via_administracion text
);


--
-- Name: medicamentos_medicamento_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.medicamentos_medicamento ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.medicamentos_medicamento_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: notificaciones_peticiones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notificaciones_peticiones (
    id_notificacion integer NOT NULL,
    id_peticion integer NOT NULL,
    id_usuario_destino integer,
    tipo character varying(50) DEFAULT 'NUEVA_PETICION'::character varying NOT NULL,
    titulo character varying(200) NOT NULL,
    mensaje text NOT NULL,
    leida boolean DEFAULT false NOT NULL,
    fecha_creacion timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    fecha_lectura timestamp without time zone
);


--
-- Name: notificaciones_peticiones_id_notificacion_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notificaciones_peticiones_id_notificacion_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notificaciones_peticiones_id_notificacion_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notificaciones_peticiones_id_notificacion_seq OWNED BY public.notificaciones_peticiones.id_notificacion;


--
-- Name: patologia; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patologia (
    id_patologia integer NOT NULL,
    nombre character varying(150) NOT NULL,
    descripcion text,
    activo boolean DEFAULT true
);


--
-- Name: patologia_id_patologia_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.patologia_id_patologia_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: patologia_id_patologia_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.patologia_id_patologia_seq OWNED BY public.patologia.id_patologia;


--
-- Name: peticiones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.peticiones (
    id_peticion integer NOT NULL,
    id_tipo integer NOT NULL,
    id_ubicacion integer,
    id_estado integer NOT NULL,
    responsable_id integer NOT NULL,
    descripcion text,
    fecha timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    prioridad character varying(20),
    fecha_asignacion timestamp without time zone,
    foto text,
    numero_radicado character varying(50),
    asignado_a_id integer,
    asignado_por_id integer
);


--
-- Name: peticiones_id_peticion_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.peticiones_id_peticion_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: peticiones_id_peticion_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.peticiones_id_peticion_seq OWNED BY public.peticiones.id_peticion;


--
-- Name: procedimiento_realizado; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.procedimiento_realizado (
    id_procedimiento integer NOT NULL,
    id_hospitalizacion integer NOT NULL,
    descripcion text,
    fecha_procedimiento date,
    hora_procedimiento time without time zone,
    medico_responsable character varying(150),
    tipo_procedimiento character varying(100)
);


--
-- Name: procedimiento_realizado_id_procedimiento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.procedimiento_realizado_id_procedimiento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: procedimiento_realizado_id_procedimiento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.procedimiento_realizado_id_procedimiento_seq OWNED BY public.procedimiento_realizado.id_procedimiento;


--
-- Name: proveedores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proveedores (
    id_proveedor integer NOT NULL,
    nombre character varying(150) NOT NULL,
    telefono character varying(30),
    direccion character varying(255),
    activo boolean DEFAULT true,
    email character varying(150)
);


--
-- Name: proveedores_id_proveedor_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.proveedores_id_proveedor_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: proveedores_id_proveedor_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.proveedores_id_proveedor_seq OWNED BY public.proveedores.id_proveedor;


--
-- Name: raza; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raza (
    id_raza integer NOT NULL,
    id_especie integer NOT NULL,
    nombre character varying(100) NOT NULL,
    descripcion text,
    activo boolean DEFAULT true NOT NULL
);


--
-- Name: raza_id_raza_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.raza_id_raza_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raza_id_raza_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.raza_id_raza_seq OWNED BY public.raza.id_raza;


--
-- Name: responsables; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.responsables (
    id_responsable integer NOT NULL,
    nombre character varying(150) NOT NULL,
    documento character varying(30),
    telefono character varying(30),
    direccion character varying(255),
    correo character varying(150),
    activo boolean DEFAULT true
);


--
-- Name: responsables_id_responsable_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.responsables_id_responsable_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: responsables_id_responsable_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.responsables_id_responsable_seq OWNED BY public.responsables.id_responsable;


--
-- Name: rol; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rol (
    id_rol integer NOT NULL,
    nombre_rol character varying(50) NOT NULL
);


--
-- Name: rol_id_rol_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rol_id_rol_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rol_id_rol_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rol_id_rol_seq OWNED BY public.rol.id_rol;


--
-- Name: salidas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.salidas (
    id_salida integer NOT NULL,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    motivo character varying(100)
);


--
-- Name: salidas_id_salida_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.salidas_id_salida_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: salidas_id_salida_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.salidas_id_salida_seq OWNED BY public.salidas.id_salida;


--
-- Name: sedes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sedes (
    id_sede integer NOT NULL,
    nombre character varying(150) NOT NULL,
    direccion character varying(255) NOT NULL,
    ciudad character varying(100) NOT NULL,
    telefono character varying(30) NOT NULL,
    codigo character varying(50),
    activo boolean DEFAULT true NOT NULL,
    fecha_creacion timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: sedes_id_sede_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sedes_id_sede_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sedes_id_sede_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sedes_id_sede_seq OWNED BY public.sedes.id_sede;


--
-- Name: seguimiento_hospitalario; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.seguimiento_hospitalario (
    id_seguimiento_hosp integer NOT NULL,
    id_hospitalizacion integer NOT NULL,
    evolucion text,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: seguimiento_hospitalario_id_seguimiento_hosp_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seguimiento_hospitalario_id_seguimiento_hosp_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: seguimiento_hospitalario_id_seguimiento_hosp_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.seguimiento_hospitalario_id_seguimiento_hosp_seq OWNED BY public.seguimiento_hospitalario.id_seguimiento_hosp;


--
-- Name: seguimiento_peticiones_visita; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.seguimiento_peticiones_visita (
    id_seguimiento integer NOT NULL,
    id_peticion integer NOT NULL,
    id_veterinario integer NOT NULL,
    id_animal integer,
    observacion text,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    numero_radicado character varying(50),
    fecha_atencion date,
    quien_reporta_otro character varying(100),
    nro_animales_atendidos integer,
    nombre_paciente character varying(50),
    peso_paciente numeric(5,2),
    esterilizacion_paciente boolean DEFAULT false,
    descripcion_paciente text,
    desparasitacion boolean DEFAULT false,
    pruebas_complementarias character varying(150),
    resultado_pruebas text,
    compromisos text,
    fundamento_legal text,
    plazo_dias_cumplimiento integer,
    nombre_funcionario character varying(150),
    funcionario_cargo character varying(100),
    notificado_nombre character varying(150),
    notificaciones_identificacion character varying(20),
    quien_reporta character varying(30),
    solicitud_atencion_por character varying(255),
    propietario_nombre character varying(150),
    propietario_cedula character varying(20),
    propietario_telefono character varying(20),
    propietario_email character varying(100),
    paciente_especie character varying(50),
    paciente_sexo character varying(10),
    paciente_color character varying(50),
    paciente_raza character varying(50),
    paciente_edad character varying(50),
    anamnesis_descripcion_queja text,
    tratamiento_realizado text,
    fecha_notificacion date,
    propietario_barrio character varying(100),
    propietario_direccion character varying(255),
    id_ubicacion_visita integer,
    notificador_nombre character varying(150),
    notificador_identificacion character varying(50),
    notificador_cargo character varying(100),
    firma_notificador text,
    firma_notificado text,
    test_distemper boolean DEFAULT false NOT NULL,
    test_parvovirus boolean DEFAULT false NOT NULL,
    test_coronavirus boolean DEFAULT false NOT NULL,
    test_sida boolean DEFAULT false NOT NULL,
    test_leucemia boolean DEFAULT false NOT NULL
);


--
-- Name: seguimiento_peticiones_visita_id_seguimiento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seguimiento_peticiones_visita_id_seguimiento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: seguimiento_peticiones_visita_id_seguimiento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.seguimiento_peticiones_visita_id_seguimiento_seq OWNED BY public.seguimiento_peticiones_visita.id_seguimiento;


--
-- Name: seguimiento_visita_funcionarios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.seguimiento_visita_funcionarios (
    id integer NOT NULL,
    id_seguimiento integer NOT NULL,
    id_usuario integer,
    nombre character varying(150) NOT NULL,
    cargo character varying(100),
    es_externo boolean DEFAULT false,
    es_principal boolean DEFAULT false,
    institucion character varying(200),
    documento_identidad character varying(50)
);


--
-- Name: seguimiento_visita_funcionarios_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seguimiento_visita_funcionarios_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: seguimiento_visita_funcionarios_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.seguimiento_visita_funcionarios_id_seq OWNED BY public.seguimiento_visita_funcionarios.id;


--
-- Name: seguimientos_clinicos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.seguimientos_clinicos (
    id_seguimiento integer NOT NULL,
    id_consulta integer NOT NULL,
    evolucion text,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    temperatura numeric(4,1),
    frecuencia_cardiaca integer,
    frecuencia_respiratoria integer,
    peso numeric(5,2),
    plan_tratamiento text
);


--
-- Name: seguimientos_clinicos_id_seguimiento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seguimientos_clinicos_id_seguimiento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: seguimientos_clinicos_id_seguimiento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.seguimientos_clinicos_id_seguimiento_seq OWNED BY public.seguimientos_clinicos.id_seguimiento;


--
-- Name: solicitudes_kit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.solicitudes_kit (
    id_solicitud bigint NOT NULL,
    tipo_solicitud character varying(20) NOT NULL,
    estado character varying(20) DEFAULT 'PENDIENTE'::character varying NOT NULL,
    id_veterinario integer NOT NULL,
    id_gestor integer,
    fecha_solicitud timestamp with time zone DEFAULT now() NOT NULL,
    fecha_resolucion timestamp with time zone,
    motivo_solicitud text NOT NULL,
    observaciones_gestor text,
    es_reposicion_total boolean DEFAULT false NOT NULL,
    CONSTRAINT chk_solicitud_estado CHECK (((estado)::text = ANY ((ARRAY['PENDIENTE'::character varying, 'APROBADA'::character varying, 'PARCIAL'::character varying, 'RECHAZADA'::character varying, 'CANCELADA'::character varying])::text[]))),
    CONSTRAINT chk_solicitud_tipo CHECK (((tipo_solicitud)::text = ANY ((ARRAY['REPOSICION'::character varying, 'DEVOLUCION'::character varying])::text[])))
);


--
-- Name: solicitudes_kit_detalles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.solicitudes_kit_detalles (
    id_detalle_solicitud bigint NOT NULL,
    id_solicitud bigint NOT NULL,
    id_medicamento integer NOT NULL,
    id_inventario_origen integer,
    cantidad_solicitada integer NOT NULL,
    cantidad_aprobada integer DEFAULT 0 NOT NULL,
    es_vencido boolean DEFAULT false NOT NULL,
    CONSTRAINT chk_sol_det_aprob_le_sol CHECK ((cantidad_aprobada <= cantidad_solicitada)),
    CONSTRAINT chk_sol_det_cant_aprob_no_neg CHECK ((cantidad_aprobada >= 0)),
    CONSTRAINT chk_sol_det_cant_sol_pos CHECK ((cantidad_solicitada > 0))
);


--
-- Name: solicitudes_kit_detalles_id_detalle_solicitud_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.solicitudes_kit_detalles_id_detalle_solicitud_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: solicitudes_kit_detalles_id_detalle_solicitud_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.solicitudes_kit_detalles_id_detalle_solicitud_seq OWNED BY public.solicitudes_kit_detalles.id_detalle_solicitud;


--
-- Name: solicitudes_kit_id_solicitud_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.solicitudes_kit_id_solicitud_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: solicitudes_kit_id_solicitud_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.solicitudes_kit_id_solicitud_seq OWNED BY public.solicitudes_kit.id_solicitud;


--
-- Name: tipo_peticion; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tipo_peticion (
    id_tipo integer NOT NULL,
    nombre character varying(100) NOT NULL,
    descripcion text,
    activo boolean DEFAULT true
);


--
-- Name: tipo_peticion_id_tipo_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tipo_peticion_id_tipo_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tipo_peticion_id_tipo_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tipo_peticion_id_tipo_seq OWNED BY public.tipo_peticion.id_tipo;


--
-- Name: tipos_eventos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tipos_eventos (
    id_tipo_evento integer NOT NULL,
    nombre character varying(100) NOT NULL,
    descripcion text,
    activo boolean DEFAULT true
);


--
-- Name: tipos_eventos_id_tipo_evento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tipos_eventos_id_tipo_evento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tipos_eventos_id_tipo_evento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tipos_eventos_id_tipo_evento_seq OWNED BY public.tipos_eventos.id_tipo_evento;


--
-- Name: tratamiento_medicamentos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tratamiento_medicamentos (
    id_tratamiento_medicamento integer NOT NULL,
    id_tratamiento integer NOT NULL,
    id_medicamento integer NOT NULL,
    dosis character varying(100),
    fecha_aplicacion date,
    via character varying(50),
    hora_administrada time without time zone,
    responsable_admin character varying(150),
    reaccion_comentarios text
);


--
-- Name: tratamiento_medicamentos_id_tratamiento_medicamento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tratamiento_medicamentos_id_tratamiento_medicamento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tratamiento_medicamentos_id_tratamiento_medicamento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tratamiento_medicamentos_id_tratamiento_medicamento_seq OWNED BY public.tratamiento_medicamentos.id_tratamiento_medicamento;


--
-- Name: tratamientos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tratamientos (
    id_tratamiento integer NOT NULL,
    id_consulta integer NOT NULL,
    descripcion character varying(255),
    producto_base character varying(150),
    dosis_basica character varying(100),
    presentacion character varying(100),
    via_administracion character varying(100),
    frecuencia_duracion character varying(100),
    materiales_utilizados text
);


--
-- Name: tratamientos_id_tratamiento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tratamientos_id_tratamiento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tratamientos_id_tratamiento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tratamientos_id_tratamiento_seq OWNED BY public.tratamientos.id_tratamiento;


--
-- Name: ubicaciones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ubicaciones (
    id_ubicacion integer NOT NULL,
    direccion character varying(255),
    latitud numeric(10,7),
    longitud numeric(10,7)
);


--
-- Name: ubicaciones_id_ubicacion_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ubicaciones_id_ubicacion_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ubicaciones_id_ubicacion_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ubicaciones_id_ubicacion_seq OWNED BY public.ubicaciones.id_ubicacion;


--
-- Name: usuarios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usuarios (
    id_usuario integer NOT NULL,
    email character varying(150) NOT NULL,
    password character varying(128) NOT NULL,
    nombre character varying(150) NOT NULL,
    apellido character varying(100) NOT NULL,
    id_rol integer NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    identificacion character varying(20),
    telefono character varying(30),
    matricula_profesional character varying(50),
    foto_perfil text,
    id_sede integer
);


--
-- Name: usuarios_id_usuario_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.usuarios_id_usuario_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: usuarios_id_usuario_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.usuarios_id_usuario_seq OWNED BY public.usuarios.id_usuario;


--
-- Name: v_inventario_general_lotes; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_inventario_general_lotes AS
 SELECT i.id_inventario,
    COALESCE(dc.id_medicamento, adm.id_medicamento) AS id_medicamento,
    m.nombre AS nombre_medicamento,
    m.principio_activo,
    m.presentacion,
    m.concentracion,
    m.tipo,
    i.cantidad_actual,
    i.fecha_vencimiento,
    i.estado,
    dc.id_compra,
    c.fecha AS fecha_compra,
    p.nombre AS nombre_proveedor,
        CASE
            WHEN (i.fecha_vencimiento IS NULL) THEN 'sin_fecha'::text
            WHEN (i.fecha_vencimiento < CURRENT_DATE) THEN 'vencido'::text
            WHEN (i.fecha_vencimiento <= (CURRENT_DATE + '30 days'::interval)) THEN 'proximo_a_vencer'::text
            ELSE 'vigente'::text
        END AS estado_vencimiento,
        CASE
            WHEN ((i.fecha_vencimiento IS NOT NULL) AND (i.fecha_vencimiento < CURRENT_DATE)) THEN true
            ELSE false
        END AS esta_vencido,
        CASE
            WHEN ((i.cantidad_actual > 0) AND ((i.fecha_vencimiento IS NULL) OR (i.fecha_vencimiento >= CURRENT_DATE)) AND (((i.estado)::text = 'Disponible'::text) OR (i.estado IS NULL))) THEN true
            ELSE false
        END AS disponible_para_asignar
   FROM ((((((public.inventarios i
     LEFT JOIN public.detalle_compra dc ON ((i.id_detalle_compra = dc.id_detalle_compra)))
     LEFT JOIN public.detalle_salida ds ON ((i.id_detalle_salida = ds.id_detalle_salida)))
     LEFT JOIN public.administracion_medicamento adm ON ((ds.id_administracion = adm.id_administracion)))
     LEFT JOIN public.medicamentos m ON ((m.id_medicamento = COALESCE(dc.id_medicamento, adm.id_medicamento))))
     LEFT JOIN public.compra c ON ((dc.id_compra = c.id_compra)))
     LEFT JOIN public.proveedores p ON ((c.id_proveedor = p.id_proveedor)));


--
-- Name: visita_animal; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.visita_animal (
    id_visita integer NOT NULL,
    id_animal integer NOT NULL,
    id_seguimiento integer,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    nro_radicado_atencion character varying(50),
    quien_reporta character varying(50),
    lugar_atencion_direccion character varying(255),
    anamnesis_queja text,
    atencion_tratamiento_campo text,
    desparasitacion_campo boolean DEFAULT false,
    pruebas_rapidas_resultado text,
    compromisos text,
    plazo_dias integer,
    nombre_notificado character varying(150),
    documento_notificado character varying(30),
    fecha_notificacion timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: visita_animal_id_visita_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.visita_animal_id_visita_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: visita_animal_id_visita_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.visita_animal_id_visita_seq OWNED BY public.visita_animal.id_visita;


--
-- Name: voluntariado_eventovoluntariado; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voluntariado_eventovoluntariado (
    id bigint NOT NULL,
    titulo character varying(150) NOT NULL,
    descripcion text NOT NULL,
    imagen character varying(100) NOT NULL,
    fecha date NOT NULL
);


--
-- Name: voluntariado_eventovoluntariado_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.voluntariado_eventovoluntariado ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.voluntariado_eventovoluntariado_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: voluntariado_postulacionvoluntariado; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voluntariado_postulacionvoluntariado (
    id bigint NOT NULL,
    correo character varying(254) NOT NULL,
    identificacion character varying(50) NOT NULL,
    nombre_completo character varying(150) NOT NULL,
    edad integer,
    telefono character varying(20) NOT NULL,
    fecha_postulacion timestamp with time zone NOT NULL,
    evento_id bigint,
    estado character varying(20) NOT NULL,
    usuario_id integer
);


--
-- Name: voluntariado_postulacionvoluntariado_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.voluntariado_postulacionvoluntariado ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.voluntariado_postulacionvoluntariado_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: voluntario_evento; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voluntario_evento (
    id_voluntario_evento integer NOT NULL,
    id_usuario integer NOT NULL,
    id_evento integer NOT NULL,
    fecha timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: voluntario_evento_id_voluntario_evento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.voluntario_evento_id_voluntario_evento_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: voluntario_evento_id_voluntario_evento_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.voluntario_evento_id_voluntario_evento_seq OWNED BY public.voluntario_evento.id_voluntario_evento;


--
-- Name: administracion_medicamento id_administracion; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.administracion_medicamento ALTER COLUMN id_administracion SET DEFAULT nextval('public.administracion_medicamento_id_administracion_seq'::regclass);


--
-- Name: animal id_animal; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.animal ALTER COLUMN id_animal SET DEFAULT nextval('public.animal_id_animal_seq'::regclass);


--
-- Name: animal_responsable id_animal_responsable; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.animal_responsable ALTER COLUMN id_animal_responsable SET DEFAULT nextval('public.animal_responsable_id_animal_responsable_seq'::regclass);


--
-- Name: compra id_compra; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compra ALTER COLUMN id_compra SET DEFAULT nextval('public.compra_id_compra_seq'::regclass);


--
-- Name: consulta id_consulta; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consulta ALTER COLUMN id_consulta SET DEFAULT nextval('public.consulta_id_consulta_seq'::regclass);


--
-- Name: detalle_compra id_detalle_compra; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_compra ALTER COLUMN id_detalle_compra SET DEFAULT nextval('public.detalle_compra_id_detalle_compra_seq'::regclass);


--
-- Name: detalle_salida id_detalle_salida; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_salida ALTER COLUMN id_detalle_salida SET DEFAULT nextval('public.detalle_salida_id_detalle_salida_seq'::regclass);


--
-- Name: diagnostico id_diagnostico; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostico ALTER COLUMN id_diagnostico SET DEFAULT nextval('public.diagnostico_id_diagnostico_seq'::regclass);


--
-- Name: diagnostico_patologia id_diagnostico_patologia; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostico_patologia ALTER COLUMN id_diagnostico_patologia SET DEFAULT nextval('public.diagnostico_patologia_id_diagnostico_patologia_seq'::regclass);


--
-- Name: especies id_especie; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.especies ALTER COLUMN id_especie SET DEFAULT nextval('public.especie_id_especie_seq'::regclass);


--
-- Name: estado_peticiones id_estado; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.estado_peticiones ALTER COLUMN id_estado SET DEFAULT nextval('public.estado_peticiones_id_estado_seq'::regclass);


--
-- Name: estados_animal id_estado_animal; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.estados_animal ALTER COLUMN id_estado_animal SET DEFAULT nextval('public.estados_animal_id_estado_animal_seq'::regclass);


--
-- Name: eventos id_evento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eventos ALTER COLUMN id_evento SET DEFAULT nextval('public.eventos_id_evento_seq'::regclass);


--
-- Name: evidencia_peticiones id_evidencia; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidencia_peticiones ALTER COLUMN id_evidencia SET DEFAULT nextval('public.evidencia_peticiones_id_evidencia_seq'::regclass);


--
-- Name: evidencia_seguimiento_visita id_evidencia; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidencia_seguimiento_visita ALTER COLUMN id_evidencia SET DEFAULT nextval('public.evidencia_seguimiento_visita_id_evidencia_seq'::regclass);


--
-- Name: examen id_examen; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.examen ALTER COLUMN id_examen SET DEFAULT nextval('public.examen_id_examen_seq'::regclass);


--
-- Name: historia_clinica id_historia; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historia_clinica ALTER COLUMN id_historia SET DEFAULT nextval('public.historia_clinica_id_historia_seq'::regclass);


--
-- Name: historial_peticiones id_historial; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones ALTER COLUMN id_historial SET DEFAULT nextval('public.historial_peticiones_id_historial_seq'::regclass);


--
-- Name: hospitalizacion_seres_sintientes id_hospitalizacion; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hospitalizacion_seres_sintientes ALTER COLUMN id_hospitalizacion SET DEFAULT nextval('public.hospitalizacion_seres_sintientes_id_hospitalizacion_seq'::regclass);


--
-- Name: inventario_consumos_registro id_consumo; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro ALTER COLUMN id_consumo SET DEFAULT nextval('public.inventario_consumos_registro_id_consumo_seq'::regclass);


--
-- Name: inventario_kardex id_kardex; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex ALTER COLUMN id_kardex SET DEFAULT nextval('public.inventario_kardex_id_kardex_seq'::regclass);


--
-- Name: inventarios id_inventario; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventarios ALTER COLUMN id_inventario SET DEFAULT nextval('public.inventarios_id_inventario_seq'::regclass);


--
-- Name: kit_veterinario_lotes id_kit_lote; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kit_veterinario_lotes ALTER COLUMN id_kit_lote SET DEFAULT nextval('public.kit_veterinario_lotes_id_kit_lote_seq'::regclass);


--
-- Name: medicamentos id_medicamento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.medicamentos ALTER COLUMN id_medicamento SET DEFAULT nextval('public.medicamentos_id_medicamento_seq'::regclass);


--
-- Name: notificaciones_peticiones id_notificacion; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notificaciones_peticiones ALTER COLUMN id_notificacion SET DEFAULT nextval('public.notificaciones_peticiones_id_notificacion_seq'::regclass);


--
-- Name: patologia id_patologia; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patologia ALTER COLUMN id_patologia SET DEFAULT nextval('public.patologia_id_patologia_seq'::regclass);


--
-- Name: peticiones id_peticion; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones ALTER COLUMN id_peticion SET DEFAULT nextval('public.peticiones_id_peticion_seq'::regclass);


--
-- Name: procedimiento_realizado id_procedimiento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procedimiento_realizado ALTER COLUMN id_procedimiento SET DEFAULT nextval('public.procedimiento_realizado_id_procedimiento_seq'::regclass);


--
-- Name: proveedores id_proveedor; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proveedores ALTER COLUMN id_proveedor SET DEFAULT nextval('public.proveedores_id_proveedor_seq'::regclass);


--
-- Name: raza id_raza; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raza ALTER COLUMN id_raza SET DEFAULT nextval('public.raza_id_raza_seq'::regclass);


--
-- Name: responsables id_responsable; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.responsables ALTER COLUMN id_responsable SET DEFAULT nextval('public.responsables_id_responsable_seq'::regclass);


--
-- Name: rol id_rol; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rol ALTER COLUMN id_rol SET DEFAULT nextval('public.rol_id_rol_seq'::regclass);


--
-- Name: salidas id_salida; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salidas ALTER COLUMN id_salida SET DEFAULT nextval('public.salidas_id_salida_seq'::regclass);


--
-- Name: sedes id_sede; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sedes ALTER COLUMN id_sede SET DEFAULT nextval('public.sedes_id_sede_seq'::regclass);


--
-- Name: seguimiento_hospitalario id_seguimiento_hosp; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_hospitalario ALTER COLUMN id_seguimiento_hosp SET DEFAULT nextval('public.seguimiento_hospitalario_id_seguimiento_hosp_seq'::regclass);


--
-- Name: seguimiento_peticiones_visita id_seguimiento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_peticiones_visita ALTER COLUMN id_seguimiento SET DEFAULT nextval('public.seguimiento_peticiones_visita_id_seguimiento_seq'::regclass);


--
-- Name: seguimiento_visita_funcionarios id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_visita_funcionarios ALTER COLUMN id SET DEFAULT nextval('public.seguimiento_visita_funcionarios_id_seq'::regclass);


--
-- Name: seguimientos_clinicos id_seguimiento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimientos_clinicos ALTER COLUMN id_seguimiento SET DEFAULT nextval('public.seguimientos_clinicos_id_seguimiento_seq'::regclass);


--
-- Name: solicitudes_kit id_solicitud; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit ALTER COLUMN id_solicitud SET DEFAULT nextval('public.solicitudes_kit_id_solicitud_seq'::regclass);


--
-- Name: solicitudes_kit_detalles id_detalle_solicitud; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit_detalles ALTER COLUMN id_detalle_solicitud SET DEFAULT nextval('public.solicitudes_kit_detalles_id_detalle_solicitud_seq'::regclass);


--
-- Name: tipo_peticion id_tipo; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tipo_peticion ALTER COLUMN id_tipo SET DEFAULT nextval('public.tipo_peticion_id_tipo_seq'::regclass);


--
-- Name: tipos_eventos id_tipo_evento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tipos_eventos ALTER COLUMN id_tipo_evento SET DEFAULT nextval('public.tipos_eventos_id_tipo_evento_seq'::regclass);


--
-- Name: tratamiento_medicamentos id_tratamiento_medicamento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tratamiento_medicamentos ALTER COLUMN id_tratamiento_medicamento SET DEFAULT nextval('public.tratamiento_medicamentos_id_tratamiento_medicamento_seq'::regclass);


--
-- Name: tratamientos id_tratamiento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tratamientos ALTER COLUMN id_tratamiento SET DEFAULT nextval('public.tratamientos_id_tratamiento_seq'::regclass);


--
-- Name: ubicaciones id_ubicacion; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ubicaciones ALTER COLUMN id_ubicacion SET DEFAULT nextval('public.ubicaciones_id_ubicacion_seq'::regclass);


--
-- Name: usuarios id_usuario; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios ALTER COLUMN id_usuario SET DEFAULT nextval('public.usuarios_id_usuario_seq'::regclass);


--
-- Name: visita_animal id_visita; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.visita_animal ALTER COLUMN id_visita SET DEFAULT nextval('public.visita_animal_id_visita_seq'::regclass);


--
-- Name: voluntario_evento id_voluntario_evento; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntario_evento ALTER COLUMN id_voluntario_evento SET DEFAULT nextval('public.voluntario_evento_id_voluntario_evento_seq'::regclass);


--
-- Name: administracion_medicamento administracion_medicamento_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.administracion_medicamento
    ADD CONSTRAINT administracion_medicamento_pkey PRIMARY KEY (id_administracion);


--
-- Name: adopciones_solicitudadopcion adopciones_solicitudadopcion_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adopciones_solicitudadopcion
    ADD CONSTRAINT adopciones_solicitudadopcion_pkey PRIMARY KEY (id);


--
-- Name: animal animal_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.animal
    ADD CONSTRAINT animal_pkey PRIMARY KEY (id_animal);


--
-- Name: animal_responsable animal_responsable_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.animal_responsable
    ADD CONSTRAINT animal_responsable_pkey PRIMARY KEY (id_animal_responsable);


--
-- Name: auth_group auth_group_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_group
    ADD CONSTRAINT auth_group_name_key UNIQUE (name);


--
-- Name: auth_group_permissions auth_group_permissions_group_id_permission_id_0cd325b0_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_group_permissions
    ADD CONSTRAINT auth_group_permissions_group_id_permission_id_0cd325b0_uniq UNIQUE (group_id, permission_id);


--
-- Name: auth_group_permissions auth_group_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_group_permissions
    ADD CONSTRAINT auth_group_permissions_pkey PRIMARY KEY (id);


--
-- Name: auth_group auth_group_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_group
    ADD CONSTRAINT auth_group_pkey PRIMARY KEY (id);


--
-- Name: auth_permission auth_permission_content_type_id_codename_01ab375a_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_permission
    ADD CONSTRAINT auth_permission_content_type_id_codename_01ab375a_uniq UNIQUE (content_type_id, codename);


--
-- Name: auth_permission auth_permission_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_permission
    ADD CONSTRAINT auth_permission_pkey PRIMARY KEY (id);


--
-- Name: auth_user_groups auth_user_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_groups
    ADD CONSTRAINT auth_user_groups_pkey PRIMARY KEY (id);


--
-- Name: auth_user_groups auth_user_groups_user_id_group_id_94350c0c_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_groups
    ADD CONSTRAINT auth_user_groups_user_id_group_id_94350c0c_uniq UNIQUE (user_id, group_id);


--
-- Name: auth_user auth_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user
    ADD CONSTRAINT auth_user_pkey PRIMARY KEY (id);


--
-- Name: auth_user_user_permissions auth_user_user_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_user_permissions
    ADD CONSTRAINT auth_user_user_permissions_pkey PRIMARY KEY (id);


--
-- Name: auth_user_user_permissions auth_user_user_permissions_user_id_permission_id_14a6b632_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_user_permissions
    ADD CONSTRAINT auth_user_user_permissions_user_id_permission_id_14a6b632_uniq UNIQUE (user_id, permission_id);


--
-- Name: auth_user auth_user_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user
    ADD CONSTRAINT auth_user_username_key UNIQUE (username);


--
-- Name: authtoken_token authtoken_token_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authtoken_token
    ADD CONSTRAINT authtoken_token_pkey PRIMARY KEY (key);


--
-- Name: authtoken_token authtoken_token_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authtoken_token
    ADD CONSTRAINT authtoken_token_user_id_key UNIQUE (user_id);


--
-- Name: compra compra_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compra
    ADD CONSTRAINT compra_pkey PRIMARY KEY (id_compra);


--
-- Name: consulta consulta_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consulta
    ADD CONSTRAINT consulta_pkey PRIMARY KEY (id_consulta);


--
-- Name: detalle_compra detalle_compra_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_compra
    ADD CONSTRAINT detalle_compra_pkey PRIMARY KEY (id_detalle_compra);


--
-- Name: detalle_salida detalle_salida_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_salida
    ADD CONSTRAINT detalle_salida_pkey PRIMARY KEY (id_detalle_salida);


--
-- Name: diagnostico_patologia diagnostico_patologia_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostico_patologia
    ADD CONSTRAINT diagnostico_patologia_pkey PRIMARY KEY (id_diagnostico_patologia);


--
-- Name: diagnostico diagnostico_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostico
    ADD CONSTRAINT diagnostico_pkey PRIMARY KEY (id_diagnostico);


--
-- Name: django_admin_log django_admin_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.django_admin_log
    ADD CONSTRAINT django_admin_log_pkey PRIMARY KEY (id);


--
-- Name: django_content_type django_content_type_app_label_model_76bd3d3b_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.django_content_type
    ADD CONSTRAINT django_content_type_app_label_model_76bd3d3b_uniq UNIQUE (app_label, model);


--
-- Name: django_content_type django_content_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.django_content_type
    ADD CONSTRAINT django_content_type_pkey PRIMARY KEY (id);


--
-- Name: django_migrations django_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.django_migrations
    ADD CONSTRAINT django_migrations_pkey PRIMARY KEY (id);


--
-- Name: django_session django_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.django_session
    ADD CONSTRAINT django_session_pkey PRIMARY KEY (session_key);


--
-- Name: especies especie_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.especies
    ADD CONSTRAINT especie_pkey PRIMARY KEY (id_especie);


--
-- Name: estado_peticiones estado_peticiones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.estado_peticiones
    ADD CONSTRAINT estado_peticiones_pkey PRIMARY KEY (id_estado);


--
-- Name: estados_animal estados_animal_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.estados_animal
    ADD CONSTRAINT estados_animal_pkey PRIMARY KEY (id_estado_animal);


--
-- Name: eventos eventos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eventos
    ADD CONSTRAINT eventos_pkey PRIMARY KEY (id_evento);


--
-- Name: evidencia_peticiones evidencia_peticiones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidencia_peticiones
    ADD CONSTRAINT evidencia_peticiones_pkey PRIMARY KEY (id_evidencia);


--
-- Name: evidencia_seguimiento_visita evidencia_seguimiento_visita_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidencia_seguimiento_visita
    ADD CONSTRAINT evidencia_seguimiento_visita_pkey PRIMARY KEY (id_evidencia);


--
-- Name: examen examen_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.examen
    ADD CONSTRAINT examen_pkey PRIMARY KEY (id_examen);


--
-- Name: historia_clinica historia_clinica_id_animal_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historia_clinica
    ADD CONSTRAINT historia_clinica_id_animal_key UNIQUE (id_animal);


--
-- Name: historia_clinica historia_clinica_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historia_clinica
    ADD CONSTRAINT historia_clinica_pkey PRIMARY KEY (id_historia);


--
-- Name: historial_peticiones historial_peticiones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones
    ADD CONSTRAINT historial_peticiones_pkey PRIMARY KEY (id_historial);


--
-- Name: hospitalizacion_seres_sintientes hospitalizacion_seres_sintientes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hospitalizacion_seres_sintientes
    ADD CONSTRAINT hospitalizacion_seres_sintientes_pkey PRIMARY KEY (id_hospitalizacion);


--
-- Name: inventario_consumos_registro inventario_consumos_registro_client_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_client_uuid_key UNIQUE (client_uuid);


--
-- Name: inventario_consumos_registro inventario_consumos_registro_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_pkey PRIMARY KEY (id_consumo);


--
-- Name: inventario_kardex inventario_kardex_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_pkey PRIMARY KEY (id_kardex);


--
-- Name: inventarios inventarios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventarios
    ADD CONSTRAINT inventarios_pkey PRIMARY KEY (id_inventario);


--
-- Name: kit_veterinario_lotes kit_veterinario_lotes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kit_veterinario_lotes
    ADD CONSTRAINT kit_veterinario_lotes_pkey PRIMARY KEY (id_kit_lote);


--
-- Name: medicamentos_medicamento medicamentos_medicamento_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.medicamentos_medicamento
    ADD CONSTRAINT medicamentos_medicamento_pkey PRIMARY KEY (id);


--
-- Name: medicamentos medicamentos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.medicamentos
    ADD CONSTRAINT medicamentos_pkey PRIMARY KEY (id_medicamento);


--
-- Name: notificaciones_peticiones notificaciones_peticiones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notificaciones_peticiones
    ADD CONSTRAINT notificaciones_peticiones_pkey PRIMARY KEY (id_notificacion);


--
-- Name: patologia patologia_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patologia
    ADD CONSTRAINT patologia_pkey PRIMARY KEY (id_patologia);


--
-- Name: peticiones peticiones_numero_radicado_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT peticiones_numero_radicado_key UNIQUE (numero_radicado);


--
-- Name: peticiones peticiones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT peticiones_pkey PRIMARY KEY (id_peticion);


--
-- Name: procedimiento_realizado procedimiento_realizado_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procedimiento_realizado
    ADD CONSTRAINT procedimiento_realizado_pkey PRIMARY KEY (id_procedimiento);


--
-- Name: proveedores proveedores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proveedores
    ADD CONSTRAINT proveedores_pkey PRIMARY KEY (id_proveedor);


--
-- Name: raza raza_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raza
    ADD CONSTRAINT raza_pkey PRIMARY KEY (id_raza);


--
-- Name: responsables responsables_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.responsables
    ADD CONSTRAINT responsables_pkey PRIMARY KEY (id_responsable);


--
-- Name: rol rol_nombre_rol_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rol
    ADD CONSTRAINT rol_nombre_rol_key UNIQUE (nombre_rol);


--
-- Name: rol rol_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rol
    ADD CONSTRAINT rol_pkey PRIMARY KEY (id_rol);


--
-- Name: salidas salidas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.salidas
    ADD CONSTRAINT salidas_pkey PRIMARY KEY (id_salida);


--
-- Name: sedes sedes_codigo_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sedes
    ADD CONSTRAINT sedes_codigo_key UNIQUE (codigo);


--
-- Name: sedes sedes_nombre_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sedes
    ADD CONSTRAINT sedes_nombre_key UNIQUE (nombre);


--
-- Name: sedes sedes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sedes
    ADD CONSTRAINT sedes_pkey PRIMARY KEY (id_sede);


--
-- Name: seguimiento_hospitalario seguimiento_hospitalario_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_hospitalario
    ADD CONSTRAINT seguimiento_hospitalario_pkey PRIMARY KEY (id_seguimiento_hosp);


--
-- Name: seguimiento_peticiones_visita seguimiento_peticiones_visita_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_peticiones_visita
    ADD CONSTRAINT seguimiento_peticiones_visita_pkey PRIMARY KEY (id_seguimiento);


--
-- Name: seguimiento_visita_funcionarios seguimiento_visita_funcionarios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_visita_funcionarios
    ADD CONSTRAINT seguimiento_visita_funcionarios_pkey PRIMARY KEY (id);


--
-- Name: seguimientos_clinicos seguimientos_clinicos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimientos_clinicos
    ADD CONSTRAINT seguimientos_clinicos_pkey PRIMARY KEY (id_seguimiento);


--
-- Name: solicitudes_kit_detalles solicitudes_kit_detalles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit_detalles
    ADD CONSTRAINT solicitudes_kit_detalles_pkey PRIMARY KEY (id_detalle_solicitud);


--
-- Name: solicitudes_kit solicitudes_kit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit
    ADD CONSTRAINT solicitudes_kit_pkey PRIMARY KEY (id_solicitud);


--
-- Name: tipo_peticion tipo_peticion_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tipo_peticion
    ADD CONSTRAINT tipo_peticion_pkey PRIMARY KEY (id_tipo);


--
-- Name: tipos_eventos tipos_eventos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tipos_eventos
    ADD CONSTRAINT tipos_eventos_pkey PRIMARY KEY (id_tipo_evento);


--
-- Name: tratamiento_medicamentos tratamiento_medicamentos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tratamiento_medicamentos
    ADD CONSTRAINT tratamiento_medicamentos_pkey PRIMARY KEY (id_tratamiento_medicamento);


--
-- Name: tratamientos tratamientos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tratamientos
    ADD CONSTRAINT tratamientos_pkey PRIMARY KEY (id_tratamiento);


--
-- Name: ubicaciones ubicaciones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ubicaciones
    ADD CONSTRAINT ubicaciones_pkey PRIMARY KEY (id_ubicacion);


--
-- Name: kit_veterinario_lotes uq_kit_vet_lote; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kit_veterinario_lotes
    ADD CONSTRAINT uq_kit_vet_lote UNIQUE (id_veterinario, id_inventario);


--
-- Name: usuarios usuarios_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_email_key UNIQUE (email);


--
-- Name: usuarios usuarios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_pkey PRIMARY KEY (id_usuario);


--
-- Name: visita_animal visita_animal_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.visita_animal
    ADD CONSTRAINT visita_animal_pkey PRIMARY KEY (id_visita);


--
-- Name: voluntariado_eventovoluntariado voluntariado_eventovoluntariado_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntariado_eventovoluntariado
    ADD CONSTRAINT voluntariado_eventovoluntariado_pkey PRIMARY KEY (id);


--
-- Name: voluntariado_postulacionvoluntariado voluntariado_postulacionvoluntariado_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntariado_postulacionvoluntariado
    ADD CONSTRAINT voluntariado_postulacionvoluntariado_pkey PRIMARY KEY (id);


--
-- Name: voluntario_evento voluntario_evento_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntario_evento
    ADD CONSTRAINT voluntario_evento_pkey PRIMARY KEY (id_voluntario_evento);


--
-- Name: adopciones_solicitudadopcion_animal_id_454a6691; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adopciones_solicitudadopcion_animal_id_454a6691 ON public.adopciones_solicitudadopcion USING btree (animal_id);


--
-- Name: auth_group_name_a6ea08ec_like; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_group_name_a6ea08ec_like ON public.auth_group USING btree (name varchar_pattern_ops);


--
-- Name: auth_group_permissions_group_id_b120cbf9; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_group_permissions_group_id_b120cbf9 ON public.auth_group_permissions USING btree (group_id);


--
-- Name: auth_group_permissions_permission_id_84c5c92e; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_group_permissions_permission_id_84c5c92e ON public.auth_group_permissions USING btree (permission_id);


--
-- Name: auth_permission_content_type_id_2f476e4b; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_permission_content_type_id_2f476e4b ON public.auth_permission USING btree (content_type_id);


--
-- Name: auth_user_groups_group_id_97559544; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_user_groups_group_id_97559544 ON public.auth_user_groups USING btree (group_id);


--
-- Name: auth_user_groups_user_id_6a12ed8b; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_user_groups_user_id_6a12ed8b ON public.auth_user_groups USING btree (user_id);


--
-- Name: auth_user_user_permissions_permission_id_1fbb5f2c; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_user_user_permissions_permission_id_1fbb5f2c ON public.auth_user_user_permissions USING btree (permission_id);


--
-- Name: auth_user_user_permissions_user_id_a95ead1b; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_user_user_permissions_user_id_a95ead1b ON public.auth_user_user_permissions USING btree (user_id);


--
-- Name: auth_user_username_6821ab7c_like; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_user_username_6821ab7c_like ON public.auth_user USING btree (username varchar_pattern_ops);


--
-- Name: authtoken_token_key_10f0b77e_like; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX authtoken_token_key_10f0b77e_like ON public.authtoken_token USING btree (key varchar_pattern_ops);


--
-- Name: django_admin_log_content_type_id_c4bce8eb; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX django_admin_log_content_type_id_c4bce8eb ON public.django_admin_log USING btree (content_type_id);


--
-- Name: django_admin_log_user_id_c564eba6; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX django_admin_log_user_id_c564eba6 ON public.django_admin_log USING btree (user_id);


--
-- Name: django_session_expire_date_a5c62663; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX django_session_expire_date_a5c62663 ON public.django_session USING btree (expire_date);


--
-- Name: django_session_session_key_c0390e0f_like; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX django_session_session_key_c0390e0f_like ON public.django_session USING btree (session_key varchar_pattern_ops);


--
-- Name: idx_consumos_origen_acta; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_consumos_origen_acta ON public.inventario_consumos_registro USING btree (id_seguimiento_visita, id_visita_animal);


--
-- Name: idx_consumos_origen_historia; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_consumos_origen_historia ON public.inventario_consumos_registro USING btree (id_consulta, id_tratamiento);


--
-- Name: idx_consumos_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_consumos_uuid ON public.inventario_consumos_registro USING btree (client_uuid);


--
-- Name: idx_consumos_vet_estado; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_consumos_vet_estado ON public.inventario_consumos_registro USING btree (id_veterinario, estado);


--
-- Name: idx_historial_peticiones_peticion; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_historial_peticiones_peticion ON public.historial_peticiones USING btree (id_peticion, fecha DESC);


--
-- Name: idx_kardex_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kardex_fecha ON public.inventario_kardex USING btree (fecha_hora DESC);


--
-- Name: idx_kardex_lote; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kardex_lote ON public.inventario_kardex USING btree (id_inventario);


--
-- Name: idx_kardex_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kardex_uuid ON public.inventario_kardex USING btree (client_uuid);


--
-- Name: idx_kardex_vet_med; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kardex_vet_med ON public.inventario_kardex USING btree (id_veterinario, id_medicamento);


--
-- Name: idx_kit_lote_inventario; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kit_lote_inventario ON public.kit_veterinario_lotes USING btree (id_inventario);


--
-- Name: idx_kit_vet_med_fifo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kit_vet_med_fifo ON public.kit_veterinario_lotes USING btree (id_veterinario, id_medicamento, fecha_compra_origen, id_inventario);


--
-- Name: idx_kit_vet_vencimiento; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kit_vet_vencimiento ON public.kit_veterinario_lotes USING btree (id_veterinario, fecha_vencimiento);


--
-- Name: idx_notif_peticiones_usuario_leida; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notif_peticiones_usuario_leida ON public.notificaciones_peticiones USING btree (id_usuario_destino, leida, fecha_creacion DESC);


--
-- Name: idx_sol_det_med; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sol_det_med ON public.solicitudes_kit_detalles USING btree (id_medicamento);


--
-- Name: idx_sol_det_solicitud; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sol_det_solicitud ON public.solicitudes_kit_detalles USING btree (id_solicitud);


--
-- Name: idx_solicitud_fecha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_solicitud_fecha ON public.solicitudes_kit USING btree (fecha_solicitud DESC);


--
-- Name: idx_solicitud_vet_estado; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_solicitud_vet_estado ON public.solicitudes_kit USING btree (id_veterinario, estado);


--
-- Name: idx_usuarios_id_sede; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usuarios_id_sede ON public.usuarios USING btree (id_sede);


--
-- Name: uq_sedes_codigo_lower; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_sedes_codigo_lower ON public.sedes USING btree (lower(TRIM(BOTH FROM codigo))) WHERE (codigo IS NOT NULL);


--
-- Name: uq_sedes_nombre_lower; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_sedes_nombre_lower ON public.sedes USING btree (lower(TRIM(BOTH FROM nombre)));


--
-- Name: voluntariado_postulacionvoluntariado_evento_id_8d0c128c; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX voluntariado_postulacionvoluntariado_evento_id_8d0c128c ON public.voluntariado_postulacionvoluntariado USING btree (evento_id);


--
-- Name: voluntariado_postulacionvoluntariado_usuario_id_ef9e591c; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX voluntariado_postulacionvoluntariado_usuario_id_ef9e591c ON public.voluntariado_postulacionvoluntariado USING btree (usuario_id);


--
-- Name: inventario_kardex trg_kardex_inmutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kardex_inmutable BEFORE DELETE OR UPDATE ON public.inventario_kardex FOR EACH ROW EXECUTE FUNCTION public.fn_proteger_inmutabilidad_kardex();


--
-- Name: inventario_kardex trg_kardex_inmutable_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kardex_inmutable_truncate BEFORE TRUNCATE ON public.inventario_kardex FOR EACH STATEMENT EXECUTE FUNCTION public.fn_proteger_truncate_kardex();


--
-- Name: adopciones_solicitudadopcion adopciones_solicitud_animal_id_454a6691_fk_animal_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adopciones_solicitudadopcion
    ADD CONSTRAINT adopciones_solicitud_animal_id_454a6691_fk_animal_id FOREIGN KEY (animal_id) REFERENCES public.animal(id_animal) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: auth_group_permissions auth_group_permissio_permission_id_84c5c92e_fk_auth_perm; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_group_permissions
    ADD CONSTRAINT auth_group_permissio_permission_id_84c5c92e_fk_auth_perm FOREIGN KEY (permission_id) REFERENCES public.auth_permission(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: auth_group_permissions auth_group_permissions_group_id_b120cbf9_fk_auth_group_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_group_permissions
    ADD CONSTRAINT auth_group_permissions_group_id_b120cbf9_fk_auth_group_id FOREIGN KEY (group_id) REFERENCES public.auth_group(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: auth_permission auth_permission_content_type_id_2f476e4b_fk_django_co; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_permission
    ADD CONSTRAINT auth_permission_content_type_id_2f476e4b_fk_django_co FOREIGN KEY (content_type_id) REFERENCES public.django_content_type(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: auth_user_groups auth_user_groups_group_id_97559544_fk_auth_group_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_groups
    ADD CONSTRAINT auth_user_groups_group_id_97559544_fk_auth_group_id FOREIGN KEY (group_id) REFERENCES public.auth_group(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: auth_user_groups auth_user_groups_user_id_6a12ed8b_fk_auth_user_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_groups
    ADD CONSTRAINT auth_user_groups_user_id_6a12ed8b_fk_auth_user_id FOREIGN KEY (user_id) REFERENCES public.auth_user(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: auth_user_user_permissions auth_user_user_permi_permission_id_1fbb5f2c_fk_auth_perm; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_user_permissions
    ADD CONSTRAINT auth_user_user_permi_permission_id_1fbb5f2c_fk_auth_perm FOREIGN KEY (permission_id) REFERENCES public.auth_permission(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: auth_user_user_permissions auth_user_user_permissions_user_id_a95ead1b_fk_auth_user_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_user_user_permissions
    ADD CONSTRAINT auth_user_user_permissions_user_id_a95ead1b_fk_auth_user_id FOREIGN KEY (user_id) REFERENCES public.auth_user(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: authtoken_token authtoken_token_user_id_35299eff_fk_auth_user_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authtoken_token
    ADD CONSTRAINT authtoken_token_user_id_35299eff_fk_auth_user_id FOREIGN KEY (user_id) REFERENCES public.auth_user(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: django_admin_log django_admin_log_content_type_id_c4bce8eb_fk_django_co; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.django_admin_log
    ADD CONSTRAINT django_admin_log_content_type_id_c4bce8eb_fk_django_co FOREIGN KEY (content_type_id) REFERENCES public.django_content_type(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: django_admin_log django_admin_log_user_id_c564eba6_fk_auth_user_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.django_admin_log
    ADD CONSTRAINT django_admin_log_user_id_c564eba6_fk_auth_user_id FOREIGN KEY (user_id) REFERENCES public.auth_user(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: evidencia_seguimiento_visita evidencia_seguimiento_visita_id_seguimiento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidencia_seguimiento_visita
    ADD CONSTRAINT evidencia_seguimiento_visita_id_seguimiento_fkey FOREIGN KEY (id_seguimiento) REFERENCES public.seguimiento_peticiones_visita(id_seguimiento) ON DELETE CASCADE;


--
-- Name: administracion_medicamento fk_am_medicamento; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.administracion_medicamento
    ADD CONSTRAINT fk_am_medicamento FOREIGN KEY (id_medicamento) REFERENCES public.medicamentos(id_medicamento);


--
-- Name: administracion_medicamento fk_am_seguimiento; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.administracion_medicamento
    ADD CONSTRAINT fk_am_seguimiento FOREIGN KEY (id_seguimiento_hosp) REFERENCES public.seguimiento_hospitalario(id_seguimiento_hosp);


--
-- Name: animal fk_animal_raza; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.animal
    ADD CONSTRAINT fk_animal_raza FOREIGN KEY (id_raza) REFERENCES public.raza(id_raza);


--
-- Name: animal_responsable fk_ar_animal; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.animal_responsable
    ADD CONSTRAINT fk_ar_animal FOREIGN KEY (id_animal) REFERENCES public.animal(id_animal);


--
-- Name: animal_responsable fk_ar_responsable; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.animal_responsable
    ADD CONSTRAINT fk_ar_responsable FOREIGN KEY (id_responsable) REFERENCES public.responsables(id_responsable);


--
-- Name: compra fk_compra_proveedor; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compra
    ADD CONSTRAINT fk_compra_proveedor FOREIGN KEY (id_proveedor) REFERENCES public.proveedores(id_proveedor);


--
-- Name: consulta fk_consulta_historia; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consulta
    ADD CONSTRAINT fk_consulta_historia FOREIGN KEY (id_historia) REFERENCES public.historia_clinica(id_historia);


--
-- Name: detalle_compra fk_dc_compra; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_compra
    ADD CONSTRAINT fk_dc_compra FOREIGN KEY (id_compra) REFERENCES public.compra(id_compra);


--
-- Name: detalle_compra fk_dc_medicamento; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_compra
    ADD CONSTRAINT fk_dc_medicamento FOREIGN KEY (id_medicamento) REFERENCES public.medicamentos(id_medicamento);


--
-- Name: diagnostico fk_diag_consulta; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostico
    ADD CONSTRAINT fk_diag_consulta FOREIGN KEY (id_consulta) REFERENCES public.consulta(id_consulta);


--
-- Name: diagnostico_patologia fk_dp_diagnostico; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostico_patologia
    ADD CONSTRAINT fk_dp_diagnostico FOREIGN KEY (id_diagnostico) REFERENCES public.diagnostico(id_diagnostico);


--
-- Name: diagnostico_patologia fk_dp_patologia; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostico_patologia
    ADD CONSTRAINT fk_dp_patologia FOREIGN KEY (id_patologia) REFERENCES public.patologia(id_patologia);


--
-- Name: detalle_salida fk_ds_administracion; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_salida
    ADD CONSTRAINT fk_ds_administracion FOREIGN KEY (id_administracion) REFERENCES public.administracion_medicamento(id_administracion);


--
-- Name: detalle_salida fk_ds_usuario; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detalle_salida
    ADD CONSTRAINT fk_ds_usuario FOREIGN KEY (id_usuario) REFERENCES public.usuarios(id_usuario);


--
-- Name: estados_animal fk_ea_animal; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.estados_animal
    ADD CONSTRAINT fk_ea_animal FOREIGN KEY (id_animal) REFERENCES public.animal(id_animal);


--
-- Name: evidencia_peticiones fk_ep_peticion; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidencia_peticiones
    ADD CONSTRAINT fk_ep_peticion FOREIGN KEY (id_peticion) REFERENCES public.peticiones(id_peticion);


--
-- Name: eventos fk_evento_tipo; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eventos
    ADD CONSTRAINT fk_evento_tipo FOREIGN KEY (id_tipo_evento) REFERENCES public.tipos_eventos(id_tipo_evento);


--
-- Name: examen fk_examen_consulta; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.examen
    ADD CONSTRAINT fk_examen_consulta FOREIGN KEY (id_consulta) REFERENCES public.consulta(id_consulta);


--
-- Name: historia_clinica fk_hc_animal; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historia_clinica
    ADD CONSTRAINT fk_hc_animal FOREIGN KEY (id_animal) REFERENCES public.animal(id_animal);


--
-- Name: hospitalizacion_seres_sintientes fk_hss_consulta; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hospitalizacion_seres_sintientes
    ADD CONSTRAINT fk_hss_consulta FOREIGN KEY (id_consulta) REFERENCES public.consulta(id_consulta);


--
-- Name: inventarios fk_inv_detalle_compra; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventarios
    ADD CONSTRAINT fk_inv_detalle_compra FOREIGN KEY (id_detalle_compra) REFERENCES public.detalle_compra(id_detalle_compra);


--
-- Name: inventarios fk_inv_detalle_salida; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventarios
    ADD CONSTRAINT fk_inv_detalle_salida FOREIGN KEY (id_detalle_salida) REFERENCES public.detalle_salida(id_detalle_salida);


--
-- Name: medicamentos fk_med_salida; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.medicamentos
    ADD CONSTRAINT fk_med_salida FOREIGN KEY (id_salida) REFERENCES public.salidas(id_salida);


--
-- Name: peticiones fk_pet_estado; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT fk_pet_estado FOREIGN KEY (id_estado) REFERENCES public.estado_peticiones(id_estado);


--
-- Name: peticiones fk_pet_responsable; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT fk_pet_responsable FOREIGN KEY (responsable_id) REFERENCES public.usuarios(id_usuario);


--
-- Name: peticiones fk_pet_tipo; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT fk_pet_tipo FOREIGN KEY (id_tipo) REFERENCES public.tipo_peticion(id_tipo);


--
-- Name: peticiones fk_pet_ubicacion; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT fk_pet_ubicacion FOREIGN KEY (id_ubicacion) REFERENCES public.ubicaciones(id_ubicacion);


--
-- Name: procedimiento_realizado fk_pr_hospitalizacion; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.procedimiento_realizado
    ADD CONSTRAINT fk_pr_hospitalizacion FOREIGN KEY (id_hospitalizacion) REFERENCES public.hospitalizacion_seres_sintientes(id_hospitalizacion);


--
-- Name: raza fk_raza_especie; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raza
    ADD CONSTRAINT fk_raza_especie FOREIGN KEY (id_especie) REFERENCES public.especies(id_especie);


--
-- Name: seguimientos_clinicos fk_sc_consulta; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimientos_clinicos
    ADD CONSTRAINT fk_sc_consulta FOREIGN KEY (id_consulta) REFERENCES public.consulta(id_consulta);


--
-- Name: seguimiento_hospitalario fk_sh_hospitalizacion; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_hospitalario
    ADD CONSTRAINT fk_sh_hospitalizacion FOREIGN KEY (id_hospitalizacion) REFERENCES public.hospitalizacion_seres_sintientes(id_hospitalizacion);


--
-- Name: seguimiento_peticiones_visita fk_spv_animal; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_peticiones_visita
    ADD CONSTRAINT fk_spv_animal FOREIGN KEY (id_animal) REFERENCES public.animal(id_animal);


--
-- Name: seguimiento_peticiones_visita fk_spv_peticion; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_peticiones_visita
    ADD CONSTRAINT fk_spv_peticion FOREIGN KEY (id_peticion) REFERENCES public.peticiones(id_peticion);


--
-- Name: seguimiento_peticiones_visita fk_spv_veterinario; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_peticiones_visita
    ADD CONSTRAINT fk_spv_veterinario FOREIGN KEY (id_veterinario) REFERENCES public.usuarios(id_usuario);


--
-- Name: tratamiento_medicamentos fk_tm_medicamento; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tratamiento_medicamentos
    ADD CONSTRAINT fk_tm_medicamento FOREIGN KEY (id_medicamento) REFERENCES public.medicamentos(id_medicamento);


--
-- Name: tratamiento_medicamentos fk_tm_tratamiento; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tratamiento_medicamentos
    ADD CONSTRAINT fk_tm_tratamiento FOREIGN KEY (id_tratamiento) REFERENCES public.tratamientos(id_tratamiento);


--
-- Name: tratamientos fk_trat_consulta; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tratamientos
    ADD CONSTRAINT fk_trat_consulta FOREIGN KEY (id_consulta) REFERENCES public.consulta(id_consulta);


--
-- Name: usuarios fk_usuario_rol; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT fk_usuario_rol FOREIGN KEY (id_rol) REFERENCES public.rol(id_rol);


--
-- Name: visita_animal fk_va_animal; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.visita_animal
    ADD CONSTRAINT fk_va_animal FOREIGN KEY (id_animal) REFERENCES public.animal(id_animal);


--
-- Name: visita_animal fk_va_seguimiento; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.visita_animal
    ADD CONSTRAINT fk_va_seguimiento FOREIGN KEY (id_seguimiento) REFERENCES public.seguimiento_peticiones_visita(id_seguimiento);


--
-- Name: voluntario_evento fk_ve_evento; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntario_evento
    ADD CONSTRAINT fk_ve_evento FOREIGN KEY (id_evento) REFERENCES public.eventos(id_evento);


--
-- Name: voluntario_evento fk_ve_usuario; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntario_evento
    ADD CONSTRAINT fk_ve_usuario FOREIGN KEY (id_usuario) REFERENCES public.usuarios(id_usuario);


--
-- Name: historial_peticiones historial_peticiones_id_estado_anterior_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones
    ADD CONSTRAINT historial_peticiones_id_estado_anterior_fkey FOREIGN KEY (id_estado_anterior) REFERENCES public.estado_peticiones(id_estado);


--
-- Name: historial_peticiones historial_peticiones_id_estado_nuevo_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones
    ADD CONSTRAINT historial_peticiones_id_estado_nuevo_fkey FOREIGN KEY (id_estado_nuevo) REFERENCES public.estado_peticiones(id_estado);


--
-- Name: historial_peticiones historial_peticiones_id_peticion_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones
    ADD CONSTRAINT historial_peticiones_id_peticion_fkey FOREIGN KEY (id_peticion) REFERENCES public.peticiones(id_peticion) ON DELETE CASCADE;


--
-- Name: historial_peticiones historial_peticiones_id_usuario_accion_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones
    ADD CONSTRAINT historial_peticiones_id_usuario_accion_fkey FOREIGN KEY (id_usuario_accion) REFERENCES public.usuarios(id_usuario);


--
-- Name: historial_peticiones historial_peticiones_id_veterinario_anterior_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones
    ADD CONSTRAINT historial_peticiones_id_veterinario_anterior_fkey FOREIGN KEY (id_veterinario_anterior) REFERENCES public.usuarios(id_usuario);


--
-- Name: historial_peticiones historial_peticiones_id_veterinario_nuevo_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historial_peticiones
    ADD CONSTRAINT historial_peticiones_id_veterinario_nuevo_fkey FOREIGN KEY (id_veterinario_nuevo) REFERENCES public.usuarios(id_usuario);


--
-- Name: inventario_consumos_registro inventario_consumos_registro_id_consulta_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_id_consulta_fkey FOREIGN KEY (id_consulta) REFERENCES public.consulta(id_consulta) ON DELETE RESTRICT;


--
-- Name: inventario_consumos_registro inventario_consumos_registro_id_medicamento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_id_medicamento_fkey FOREIGN KEY (id_medicamento) REFERENCES public.medicamentos(id_medicamento) ON DELETE RESTRICT;


--
-- Name: inventario_consumos_registro inventario_consumos_registro_id_seguimiento_visita_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_id_seguimiento_visita_fkey FOREIGN KEY (id_seguimiento_visita) REFERENCES public.seguimiento_peticiones_visita(id_seguimiento) ON DELETE RESTRICT;


--
-- Name: inventario_consumos_registro inventario_consumos_registro_id_tratamiento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_id_tratamiento_fkey FOREIGN KEY (id_tratamiento) REFERENCES public.tratamientos(id_tratamiento) ON DELETE RESTRICT;


--
-- Name: inventario_consumos_registro inventario_consumos_registro_id_veterinario_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_id_veterinario_fkey FOREIGN KEY (id_veterinario) REFERENCES public.usuarios(id_usuario) ON DELETE RESTRICT;


--
-- Name: inventario_consumos_registro inventario_consumos_registro_id_visita_animal_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_id_visita_animal_fkey FOREIGN KEY (id_visita_animal) REFERENCES public.visita_animal(id_visita) ON DELETE RESTRICT;


--
-- Name: inventario_consumos_registro inventario_consumos_registro_resuelto_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_consumos_registro
    ADD CONSTRAINT inventario_consumos_registro_resuelto_por_fkey FOREIGN KEY (resuelto_por) REFERENCES public.usuarios(id_usuario) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_consulta_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_consulta_fkey FOREIGN KEY (id_consulta) REFERENCES public.consulta(id_consulta) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_inventario_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_inventario_fkey FOREIGN KEY (id_inventario) REFERENCES public.inventarios(id_inventario) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_kardex_revertido_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_kardex_revertido_fkey FOREIGN KEY (id_kardex_revertido) REFERENCES public.inventario_kardex(id_kardex) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_medicamento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_medicamento_fkey FOREIGN KEY (id_medicamento) REFERENCES public.medicamentos(id_medicamento) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_seguimiento_visita_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_seguimiento_visita_fkey FOREIGN KEY (id_seguimiento_visita) REFERENCES public.seguimiento_peticiones_visita(id_seguimiento) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_solicitud_kit_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_solicitud_kit_fkey FOREIGN KEY (id_solicitud_kit) REFERENCES public.solicitudes_kit(id_solicitud) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_tratamiento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_tratamiento_fkey FOREIGN KEY (id_tratamiento) REFERENCES public.tratamientos(id_tratamiento) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_usuario_ejecuta_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_usuario_ejecuta_fkey FOREIGN KEY (id_usuario_ejecuta) REFERENCES public.usuarios(id_usuario) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_veterinario_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_veterinario_fkey FOREIGN KEY (id_veterinario) REFERENCES public.usuarios(id_usuario) ON DELETE RESTRICT;


--
-- Name: inventario_kardex inventario_kardex_id_visita_animal_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventario_kardex
    ADD CONSTRAINT inventario_kardex_id_visita_animal_fkey FOREIGN KEY (id_visita_animal) REFERENCES public.visita_animal(id_visita) ON DELETE RESTRICT;


--
-- Name: kit_veterinario_lotes kit_veterinario_lotes_id_inventario_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kit_veterinario_lotes
    ADD CONSTRAINT kit_veterinario_lotes_id_inventario_fkey FOREIGN KEY (id_inventario) REFERENCES public.inventarios(id_inventario) ON DELETE RESTRICT;


--
-- Name: kit_veterinario_lotes kit_veterinario_lotes_id_medicamento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kit_veterinario_lotes
    ADD CONSTRAINT kit_veterinario_lotes_id_medicamento_fkey FOREIGN KEY (id_medicamento) REFERENCES public.medicamentos(id_medicamento) ON DELETE RESTRICT;


--
-- Name: kit_veterinario_lotes kit_veterinario_lotes_id_veterinario_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kit_veterinario_lotes
    ADD CONSTRAINT kit_veterinario_lotes_id_veterinario_fkey FOREIGN KEY (id_veterinario) REFERENCES public.usuarios(id_usuario) ON DELETE RESTRICT;


--
-- Name: notificaciones_peticiones notificaciones_peticiones_id_peticion_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notificaciones_peticiones
    ADD CONSTRAINT notificaciones_peticiones_id_peticion_fkey FOREIGN KEY (id_peticion) REFERENCES public.peticiones(id_peticion) ON DELETE CASCADE;


--
-- Name: notificaciones_peticiones notificaciones_peticiones_id_usuario_destino_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notificaciones_peticiones
    ADD CONSTRAINT notificaciones_peticiones_id_usuario_destino_fkey FOREIGN KEY (id_usuario_destino) REFERENCES public.usuarios(id_usuario) ON DELETE CASCADE;


--
-- Name: peticiones peticiones_asignado_a_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT peticiones_asignado_a_id_fkey FOREIGN KEY (asignado_a_id) REFERENCES public.usuarios(id_usuario);


--
-- Name: peticiones peticiones_asignado_por_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.peticiones
    ADD CONSTRAINT peticiones_asignado_por_id_fkey FOREIGN KEY (asignado_por_id) REFERENCES public.usuarios(id_usuario);


--
-- Name: seguimiento_peticiones_visita seguimiento_peticiones_visita_id_ubicacion_visita_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_peticiones_visita
    ADD CONSTRAINT seguimiento_peticiones_visita_id_ubicacion_visita_fkey FOREIGN KEY (id_ubicacion_visita) REFERENCES public.ubicaciones(id_ubicacion);


--
-- Name: seguimiento_visita_funcionarios seguimiento_visita_funcionarios_id_seguimiento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_visita_funcionarios
    ADD CONSTRAINT seguimiento_visita_funcionarios_id_seguimiento_fkey FOREIGN KEY (id_seguimiento) REFERENCES public.seguimiento_peticiones_visita(id_seguimiento) ON DELETE CASCADE;


--
-- Name: seguimiento_visita_funcionarios seguimiento_visita_funcionarios_id_usuario_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_visita_funcionarios
    ADD CONSTRAINT seguimiento_visita_funcionarios_id_usuario_fkey FOREIGN KEY (id_usuario) REFERENCES public.usuarios(id_usuario) ON DELETE SET NULL;


--
-- Name: solicitudes_kit_detalles solicitudes_kit_detalles_id_inventario_origen_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit_detalles
    ADD CONSTRAINT solicitudes_kit_detalles_id_inventario_origen_fkey FOREIGN KEY (id_inventario_origen) REFERENCES public.inventarios(id_inventario) ON DELETE RESTRICT;


--
-- Name: solicitudes_kit_detalles solicitudes_kit_detalles_id_medicamento_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit_detalles
    ADD CONSTRAINT solicitudes_kit_detalles_id_medicamento_fkey FOREIGN KEY (id_medicamento) REFERENCES public.medicamentos(id_medicamento) ON DELETE RESTRICT;


--
-- Name: solicitudes_kit_detalles solicitudes_kit_detalles_id_solicitud_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit_detalles
    ADD CONSTRAINT solicitudes_kit_detalles_id_solicitud_fkey FOREIGN KEY (id_solicitud) REFERENCES public.solicitudes_kit(id_solicitud) ON DELETE CASCADE;


--
-- Name: solicitudes_kit solicitudes_kit_id_gestor_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit
    ADD CONSTRAINT solicitudes_kit_id_gestor_fkey FOREIGN KEY (id_gestor) REFERENCES public.usuarios(id_usuario) ON DELETE RESTRICT;


--
-- Name: solicitudes_kit solicitudes_kit_id_veterinario_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_kit
    ADD CONSTRAINT solicitudes_kit_id_veterinario_fkey FOREIGN KEY (id_veterinario) REFERENCES public.usuarios(id_usuario) ON DELETE RESTRICT;


--
-- Name: usuarios usuarios_id_sede_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_id_sede_fkey FOREIGN KEY (id_sede) REFERENCES public.sedes(id_sede) ON DELETE SET NULL;


--
-- Name: voluntariado_postulacionvoluntariado voluntariado_postula_evento_id_8d0c128c_fk_voluntari; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntariado_postulacionvoluntariado
    ADD CONSTRAINT voluntariado_postula_evento_id_8d0c128c_fk_voluntari FOREIGN KEY (evento_id) REFERENCES public.voluntariado_eventovoluntariado(id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: voluntariado_postulacionvoluntariado voluntariado_postula_usuario_id_ef9e591c_fk_usuarios_; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voluntariado_postulacionvoluntariado
    ADD CONSTRAINT voluntariado_postula_usuario_id_ef9e591c_fk_usuarios_ FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id_usuario) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: especies; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.especies ENABLE ROW LEVEL SECURITY;

--
-- Name: inventario_consumos_registro; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inventario_consumos_registro ENABLE ROW LEVEL SECURITY;

--
-- Name: inventario_kardex; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inventario_kardex ENABLE ROW LEVEL SECURITY;

--
-- Name: kit_veterinario_lotes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.kit_veterinario_lotes ENABLE ROW LEVEL SECURITY;

--
-- Name: raza; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.raza ENABLE ROW LEVEL SECURITY;

--
-- Name: rol; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.rol ENABLE ROW LEVEL SECURITY;

--
-- Name: sedes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sedes ENABLE ROW LEVEL SECURITY;

--
-- Name: solicitudes_kit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.solicitudes_kit ENABLE ROW LEVEL SECURITY;

--
-- Name: solicitudes_kit_detalles; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.solicitudes_kit_detalles ENABLE ROW LEVEL SECURITY;

--
-- Name: usuarios; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.usuarios ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--


