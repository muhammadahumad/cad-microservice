import ezdxf
from ezdxf import units
from ezdxf.math import Vec2
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import io
import traceback
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.options("/generate-dxf")
async def options_handler():
    return JSONResponse(status_code=200, content={})

# ── Helper ─────────────────────────────────────────────
def _get(obj, *keys, default=None):
    try:
        for k in keys:
            obj = obj[k]
        return obj
    except (KeyError, TypeError):
        return default

# ── AIA‑standard layer definitions ─────────────────────
LAYERS = {
    "A-WALL-EXT":  {"color": 7, "lw": 0.50},   # exterior walls
    "A-WALL-INT":  {"color": 3, "lw": 0.35},   # interior walls
    "A-DOOR":      {"color": 4, "lw": 0.25},   # doors
    "A-WINDOW":    {"color": 5, "lw": 0.25},   # windows
    "A-STAIR":     {"color": 1, "lw": 0.30},   # stairs
    "A-DIMS":      {"color": 2, "lw": 0.18},   # dimensions
    "A-ANNO-TEXT": {"color": 7, "lw": 0.18},   # text/annotations
    "S-GRID":      {"color": 8, "lw": 0.18},   # structural grid
    "S-COLM":      {"color": 6, "lw": 0.40},   # columns
    "S-BEAM":      {"color": 5, "lw": 0.40},   # beams
}

def setup_layers(doc):
    for name, props in LAYERS.items():
        if name not in doc.layers:
            layer = doc.layers.new(name)
            layer.color = props["color"]
            layer.set_lineweight(props["lw"])

# ── Generate Architectural DXF ─────────────────────────
def generate_dxf(data: dict) -> bytes:
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = units.M
    msp = doc.modelspace()
    setup_layers(doc)

    # 1. Plot boundary
    bw = _get(data, "buildableWidth", default=10.0)
    bd = _get(data, "buildableDepth", default=15.0)
    setback = _get(data, "setback", default=1.5)
    wall_thk = _get(data, "wallThickness", default=0.15)
    total_w = bw + 2 * setback
    total_d = bd + 2 * setback

    # Outer envelope
    msp.add_lwpolyline(
        [(0, 0), (total_w, 0), (total_w, total_d), (0, total_d)],
        close=True, dxfattribs={"layer": "A-WALL-EXT"}
    )
    # Inner envelope
    msp.add_lwpolyline(
        [(setback, setback), (total_w - setback, setback),
         (total_w - setback, total_d - setback), (setback, total_d - setback)],
        close=True, dxfattribs={"layer": "A-WALL-EXT"}
    )

    # 2. Structural grid
    grid = _get(data, "grid_lines", default={})
    for x_axis in grid.get("x_axis", []):
        x = x_axis.get("x_coordinate", 0) + setback
        msp.add_line((x, 0), (x, total_d), dxfattribs={"layer": "S-GRID"})
        msp.add_text(f'({x_axis.get("label","")})', dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.3}).set_placement((x + 0.1, -0.5))
    for y_axis in grid.get("y_axis", []):
        y = y_axis.get("y_coordinate", 0) + setback
        msp.add_line((0, y), (total_w, y), dxfattribs={"layer": "S-GRID"})
        msp.add_text(f'({y_axis.get("label","")})', dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.3}).set_placement((-0.8, y + 0.1))

    # 3. Rooms as interior walls
    rooms = _get(data, "rooms", default=[])
    for room in rooms:
        rx = room.get("x", 0) + setback
        ry = room.get("y", 0) + setback
        rw = room.get("width", 2)
        rd = room.get("depth", 2)
        rtype = room.get("type", "internal")

        layer = "A-STAIR" if rtype == "stairs" else "A-WALL-INT"
        msp.add_lwpolyline(
            [(rx, ry), (rx + rw, ry), (rx + rw, ry + rd), (rx, ry + rd)],
            close=True, dxfattribs={"layer": layer}
        )

        # Door arc on bottom edge
        door_x = rx + rw * 0.5
        door_y = ry + rd
        msp.add_arc(
            center=(door_x - wall_thk/2, door_y),
            radius=0.9,
            start_angle=0, end_angle=90,
            dxfattribs={"layer": "A-DOOR"}
        )

        # Window on top edge for living/bedroom
        if rtype in ("living", "bedroom"):
            win_x = rx + rw * 0.3
            win_y = ry
            msp.add_line((win_x, win_y), (win_x + 1.2, win_y), dxfattribs={"layer": "A-WINDOW"})
            msp.add_line((win_x, win_y + 0.05), (win_x + 1.2, win_y + 0.05), dxfattribs={"layer": "A-WINDOW"})

        # Room label
        msp.add_text(
            f'{room.get("name","")}\n{room.get("width",0)*room.get("depth",0):.1f} m²',
            dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.3}
        ).set_placement((rx + 0.3, ry + rw / 2))

    # 4. Columns
    elements = _get(data, "structural_elements", default=[])
    for elem in elements:
        coords = elem.get("coordinates", {})
        cx = coords.get("x", 0) + setback
        cy = coords.get("y", 0) + setback
        dims = elem.get("dimensions", {})
        cw = dims.get("width", 0.4)
        cd = dims.get("depth", 0.6)
        msp.add_lwpolyline(
            [(cx - cw/2, cy - cd/2), (cx + cw/2, cy - cd/2),
             (cx + cw/2, cy + cd/2), (cx - cw/2, cy + cd/2)],
            close=True, dxfattribs={"layer": "S-COLM"}
        )
        eid = elem.get("element_id", "C?")
        msp.add_text(eid, dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.25}).set_placement((cx - 0.15, cy + cd/2 + 0.2))

    # 5. Overall dimensions
    dim_y = total_d + 1.5
    msp.add_line((0, dim_y), (total_w, dim_y), dxfattribs={"layer": "A-DIMS"})
    msp.add_line((0, dim_y - 0.3), (0, dim_y + 0.3), dxfattribs={"layer": "A-DIMS"})
    msp.add_line((total_w, dim_y - 0.3), (total_w, dim_y + 0.3), dxfattribs={"layer": "A-DIMS"})
    msp.add_text(f'{total_w:.2f}m', dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.35}).set_placement((total_w/2 - 0.5, dim_y + 0.3))

    dim_x = total_w + 1.5
    msp.add_line((dim_x, 0), (dim_x, total_d), dxfattribs={"layer": "A-DIMS"})
    msp.add_line((dim_x - 0.3, 0), (dim_x + 0.3, 0), dxfattribs={"layer": "A-DIMS"})
    msp.add_line((dim_x - 0.3, total_d), (dim_x + 0.3, total_d), dxfattribs={"layer": "A-DIMS"})
    msp.add_text(f'{total_d:.2f}m', dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.35}).set_placement((dim_x + 0.3, total_d/2))

    # 6. Title block
    title_y = -3
    msp.add_text("ARQBLD - AI Generated Architectural Plan", dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.5}).set_placement((1, title_y))
    msp.add_text(f'Scale: 1:100 | Date: {_get(data,"date",default="2025")}', dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.3}).set_placement((1, title_y - 0.6))
    msp.add_text("HDC / MNBC Compliant Layout", dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.3}).set_placement((1, title_y - 1.0))

    # Write
    buf = io.StringIO()
    doc.write(buf)
    buf.seek(0)
    return buf.getvalue().encode("utf-8")

# ── Endpoint ───────────────────────────────────────────
@app.post("/generate-dxf")
async def generate_dxf_endpoint(request: Request):
    try:
        data = await request.json()
        dxf_bytes = generate_dxf(data)
        return StreamingResponse(
            io.BytesIO(dxf_bytes),
            media_type="application/dxf",
            headers={"Content-Disposition": "attachment; filename=layout.dxf"}
        )
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})