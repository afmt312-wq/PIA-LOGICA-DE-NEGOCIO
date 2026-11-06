
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pandas as pd
from decimal import Decimal

app = FastAPI(title="FarmaExpress API - PIA Lógica de Negocio")

# ==========================================
# 1. CARGA DE DATOS (DATA INFRASTRUCTURE)
# ==========================================
actores_df = pd.read_csv("datos/actores.csv").set_index("actor_id")
catalogo_df = pd.read_csv("datos/catalogo.csv").set_index("recurso_id")
operaciones_df = pd.read_csv("datos/operaciones.csv").set_index("operacion_id")
detalle_df = pd.read_csv("datos/detalle_operaciones.csv")

# ==========================================
# 2. PATRÓN SPECIFICATION (REGLAS DE NEGOCIO)
# ==========================================
class Specification:
    def is_satisfied_by(self, candidate) -> bool: pass
    def __and__(self, other): return AndSpecification(self, other)
    def __or__(self, other): return OrSpecification(self, other)
    def __invert__(self): return NotSpecification(self)

class AndSpecification(Specification):
    def __init__(self, spec1, spec2): self.spec1, self.spec2 = spec1, spec2
    def is_satisfied_by(self, candidate) -> bool:
        return self.spec1.is_satisfied_by(candidate) and self.spec2.is_satisfied_by(candidate)

class NotSpecification(Specification):
    def __init__(self, spec): self.spec = spec
    def is_satisfied_by(self, candidate) -> bool:
        return not self.spec.is_satisfied_by(candidate)

# Reglas Específicas
class ActorActivoSpec(Specification):
    def is_satisfied_by(self, operacion) -> bool:
        actor = actores_df.loc[operacion['actor_id']]
        return bool(actor['activo'])

class TieneLineasSpec(Specification):
    def is_satisfied_by(self, operacion) -> bool:
        return len(detalle_df[detalle_df['operacion_id'] == operacion.name]) > 0

class AutorizadoSpec(Specification):
    def is_satisfied_by(self, operacion) -> bool:
        return bool(operacion['autorizado'])

# Combinación de Especificaciones exigida en el PIA
class PuedeProcesarseSpec(Specification):
    def is_satisfied_by(self, operacion) -> bool:
        regla = ActorActivoSpec() & TieneLineasSpec() & AutorizadoSpec()
        return regla.is_satisfied_by(operacion)

# ==========================================
# 3. MÁQUINA DE ESTADOS Y EVENTOS DE DOMINIO
# ==========================================
TRANSICIONES_VALIDAS = {
    "CREADO": ["VALIDADO", "CANCELADO"],
    "VALIDADO": ["PREPARADO", "CANCELADO"],
    "PREPARADO": ["ENTREGADO", "CANCELADO"],
    "ENTREGADO": [],
    "CANCELADO": []
}

class EstadoRequest(BaseModel):
    nuevo_estado: str

@app.post("/operaciones/{operacion_id}/transicion")
def cambiar_estado(operacion_id: int, req: EstadoRequest):
    if operacion_id not in operaciones_df.index:
        raise HTTPException(status_code=404, detail="Operación no encontrada")
    
    operacion = operaciones_df.loc[operacion_id]
    estado_actual = operacion['estado_inicial']
    nuevo_estado = req.nuevo_estado.upper()
    
    # Validar Transición
    if nuevo_estado not in TRANSICIONES_VALIDAS.get(estado_actual, []):
        raise HTTPException(status_code=409, detail=f"Transición inválida de {estado_actual} a {nuevo_estado}")
    
    # Validar Regla de Negocio al pasar a VALIDADO
    if nuevo_estado == "VALIDADO":
        if not PuedeProcesarseSpec().is_satisfied_by(operacion):
            raise HTTPException(status_code=422, detail="Fallo la regla: puede_procesarse")
        
        # Validar Stock e Importes (Catálogo y Detalle)
        lineas = detalle_df[detalle_df['operacion_id'] == operacion_id]
        total_mxn = Decimal('0.0')
        for _, linea in lineas.iterrows():
            prod = catalogo_df.loc[linea['recurso_id']]
            if not prod['habilitado']:
                raise HTTPException(status_code=422, detail="El producto farmacéutico no está habilitado")
            if prod['capacidad_o_stock'] < linea['cantidad']:
                raise HTTPException(status_code=422, detail="Stock insuficiente en inventario")
            total_mxn += Decimal(str(prod['precio_mxn'])) * Decimal(str(linea['cantidad']))
            
    # Disparar Domain Event
    print(f"[DOMAIN EVENT] Venta {operacion_id} transicionó a {nuevo_estado}. Total calculado.")
    
    # Persistencia en memoria
    operaciones_df.at[operacion_id, 'estado_inicial'] = nuevo_estado
    return {"status": "success", "operacion_id": operacion_id, "estado": nuevo_estado}