import ezdxf
from ezdxf import units
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import io
import traceback
import json
from shapely.geometry import Polygon, Point, LineString
from shapely.ops import unary_union
import numpy as np
from scipy.spatial import ConvexHull

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

@app.options("/solve-layout")
async def options_handler_solve():
    return JSONResponse(status_code=200, content={})

@app.options("/parse-dxf")
async def options_handler_parse():
    return JSONResponse(status_code=200, content={})

# ── Helpers ─────────────────────────────────────────────
def _get(obj, *keys, default=None):
    try:
        for k in keys:
            obj = obj[k]
        return obj
    except (KeyError, TypeError):
        return default

LAYERS = {
    "A-WALL-EXT":  {"color": 7, "lw": 0.50},
    "A-WALL-INT":  {"color": 3, "lw": 0.35},
    "A-DOOR":      {"color": 4, "lw": 0.25},
    "A-WINDOW":    {"color": 5, "lw": 0.25},
    "A-STAIR":     {"color": 1, "lw": 0.30},
    "A-DIMS":      {"color": 2, "lw": 0.18},
    "A-ANNO-TEXT": {"color": 7, "lw": 0.18},
    "S-GRID":      {"color": 8, "lw": 0.18},
    "S-COLM":      {"color": 6, "lw": 0.40},
    "S-BEAM":      {"color": 5, "lw": 0.40},
}

def setup_layers(doc):
    for name, props in LAYERS.items():
        if name not in doc.layers:
            layer = doc.layers.new(name)
            layer.color = props["color"]
            layer.lineweight = props["lw"]

# ── Generate Professional Engineering DXF ───────────────
def generate_dxf(data: dict) -> bytes:
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = units.M
    msp = doc.modelspace()
    setup_layers(doc)

    bw = _get(data, "buildableWidth", default=10.0)
    bd = _get(data, "buildableDepth", default=15.0)
    setback = _get(data, "setback", default=1.5)
    total_w = bw + 2 * setback
    total_d = bd + 2 * setback

    # 1. Outer envelope (thick external wall)
    msp.add_lwpolyline(
        [(0, 0), (total_w, 0), (total_w, total_d), (0, total_d)],
        close=True, dxfattribs={"layer": "A-WALL-EXT"}
    )

    # 2. 9m × 9m structural grid with labels
    grid_size = 9.0
    for x in np.arange(0, total_w + 0.1, grid_size):
        msp.add_line((x, 0), (x, total_d), dxfattribs={"layer": "S-GRID"})
        msp.add_text(f"{x:.1f}", dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.35}).set_placement((x + 0.2, -0.8))

    for y in np.arange(0, total_d + 0.1, grid_size):
        msp.add_line((0, y), (total_w, y), dxfattribs={"layer": "S-GRID"})
        msp.add_text(f"{y:.1f}", dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.35}).set_placement((-0.9, y + 0.2))

    # 3. Rooms with proper layers
    rooms = _get(data, "rooms", default=[])
    for room in rooms:
        rx = room.get("x", 0) + setback
        ry = room.get("y", 0) + setback
        rw = room.get("width", 2)
        rd = room.get("depth", 2)
        rtype = room.get("type", "internal")
        name = room.get("name", "Room")

        layer = "A-STAIR" if rtype == "stairs" else "A-WALL-INT"
        msp.add_lwpolyline(
            [(rx, ry), (rx + rw, ry), (rx + rw, ry + rd), (rx, ry + rd)],
            close=True, dxfattribs={"layer": layer}
        )

        # Room label with area
        area = rw * rd
        msp.add_text(
            f"{name}\n{area:.1f} m²",
            dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.35}
        ).set_placement((rx + 0.4, ry + rd / 2))

    # 4. Columns on grid intersections
    for x in np.arange(1.0, total_w, 9.0):
        for y in np.arange(1.0, total_d, 9.0):
            msp.add_circle((x, y), 0.25, dxfattribs={"layer": "S-COLM"})
            msp.add_text("C", dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.28}).set_placement((x - 0.15, y + 0.4))

    # 5. Professional title block + stamp area
    title_y = -4.5
    msp.add_text("ARQBLD - AI Generated Professional Floor Plan",
                 dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.6}).set_placement((1, title_y))
    msp.add_text(f"Project: {data.get('projectName', 'Untitled')}",
                 dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.35}).set_placement((1, title_y - 0.8))
    msp.add_text(f"Scale: 1:100 | Date: {data.get('date', '2025')}",
                 dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.3}).set_placement((1, title_y - 1.2))
    msp.add_text("HDC Zone 2 | MNBC Compliant | Rear Core Typology",
                 dxfattribs={"layer": "A-ANNO-TEXT", "height": 0.3}).set_placement((1, title_y - 1.6))

    # Stamp box (empty for approval)
    msp.add_lwpolyline(
        [(total_w - 4.5, title_y - 2.5), (total_w - 0.5, title_y - 2.5),
         (total_w - 0.5, title_y + 1.0), (total_w - 4.5, title_y + 1.0)],
        close=True, dxfattribs={"layer": "A-ANNO-TEXT"}
    )

    buf = io.StringIO()
    doc.write(buf)
    buf.seek(0)
    return buf.getvalue().encode("utf-8")

# ── Geometric Solver ───────────────────────────────────────
def snap_to_grid(value, grid_size=0.1):
    return round(value / grid_size) * grid_size

def room_to_polygon(x, y, w, d):
    return Polygon([(x, y), (x + w, y), (x + w, y + d), (x, y + d)])

def polygon_to_room(poly):
    bounds = poly.bounds
    return bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]

def snap_polygon(poly, grid_size=0.1):
    def snap_point(p):
        return (snap_to_grid(p[0], grid_size), snap_to_grid(p[1], grid_size))
    snapped = [snap_point(p) for p in poly.exterior.coords[:-1]]
    return Polygon(snapped)

def resolve_overlaps(rooms, buildable_poly, max_iterations=50):
    polygons = [room_to_polygon(r['x'], r['y'], r['width'], r['depth']) for r in rooms]
    for _ in range(max_iterations):
        moved = False
        for i in range(len(polygons)):
            for j in range(i + 1, len(polygons)):
                p1, p2 = polygons[i], polygons[j]
                if p1.intersects(p2) and not p1.touches(p2):
                    overlap = p1.intersection(p2).centroid
                    c1 = p1.centroid
                    dx = c1.x - overlap.x
                    dy = c1.y - overlap.y
                    if abs(dx) > abs(dy):
                        p1_new = Polygon([(x + 0.1, y) if dx > 0 else (x - 0.1, y) for x, y in p1.exterior.coords])
                        p2_new = Polygon([(x - 0.1, y) if dx > 0 else (x + 0.1, y) for x, y in p2.exterior.coords])
                    else:
                        p1_new = Polygon([(x, y + 0.1) if dy > 0 else (x, y - 0.1) for x, y in p1.exterior.coords])
                        p2_new = Polygon([(x, y - 0.1) if dy > 0 else (x, y + 0.1) for x, y in p2.exterior.coords])
                    if buildable_poly.contains(p1_new) and buildable_poly.contains(p2_new):
                        polygons[i], polygons[j] = p1_new, p2_new
                        moved = True
        if not moved:
            break
    return polygons

def merge_walls(polygons, threshold=0.15):
    merged = []
    used = set()
    for i, p1 in enumerate(polygons):
        if i in used:
            continue
        for j, p2 in enumerate(polygons):
            if j <= i or j in used:
                continue
            if p1.distance(p2) < threshold:
                combined = unary_union([p1, p2])
                merged.append(combined)
                used.add(i)
                used.add(j)
                break
        if i not in used:
            merged.append(p1)
    return merged

def clean_layout(data: dict) -> dict:
    grid_size = 0.1
    buildable_w = data.get('buildableWidth', 10)
    buildable_d = data.get('buildableDepth', 15)
    setback = data.get('setback', 1.5)
    buildable_poly = Polygon([
        (setback, setback),
        (buildable_w - setback, setback),
        (buildable_w - setback, buildable_d - setback),
        (setback, buildable_d - setback)
    ])
    rooms = data.get('rooms', [])
    if not rooms:
        return data

    polygons = [room_to_polygon(r['x'], r['y'], r['width'], r['depth']) for r in rooms]
    polygons = [snap_polygon(p, grid_size) for p in polygons]
    polygons = resolve_overlaps(rooms, buildable_poly)
    polygons = merge_walls(polygons, threshold=0.15)

    new_rooms = []
    for i, poly in enumerate(polygons):
        x, y, w, d = polygon_to_room(poly)
        r = rooms[i].copy()
        r['x'] = snap_to_grid(x, grid_size)
        r['y'] = snap_to_grid(y, grid_size)
        r['width'] = snap_to_grid(w, grid_size)
        r['depth'] = snap_to_grid(d, grid_size)
        new_rooms.append(r)

    data['rooms'] = new_rooms
    return data

@app.post("/solve-layout")
async def solve_layout_endpoint(request: Request):
    try:
        data = await request.json()
        cleaned = clean_layout(data)
        return JSONResponse(content=cleaned, status_code=200)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

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

# ── NEW: DXF Import endpoint ─────────────────────────────
@app.post("/parse-dxf")
async def parse_dxf(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        doc = ezdxf.read(io.StringIO(contents.decode('utf-8')))
        msp = doc.modelspace()

        entities = []
        for e in msp:
            layer = e.dxf.layer
            if e.dxftype() == 'LINE':
                entities.append({
                    'type': 'line',
                    'layer': layer,
                    'start': {'x': e.dxf.start.x, 'y': e.dxf.start.y},
                    'end':   {'x': e.dxf.end.x,   'y': e.dxf.end.y}
                })
            elif e.dxftype() in ('LWPOLYLINE', 'POLYLINE'):
                pts = []
                if e.dxftype() == 'LWPOLYLINE':
                    pts = [(v[0], v[1]) for v in e.get_points()]
                else:
                    pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                entities.append({
                    'type': 'polyline',
                    'layer': layer,
                    'closed': e.closed if hasattr(e, 'closed') else False,
                    'points': [{'x': p[0], 'y': p[1]} for p in pts]
                })
            elif e.dxftype() == 'CIRCLE':
                entities.append({
                    'type': 'circle',
                    'layer': layer,
                    'center': {'x': e.dxf.center.x, 'y': e.dxf.center.y},
                    'radius': e.dxf.radius
                })
            elif e.dxftype() == 'TEXT':
                entities.append({
                    'type': 'text',
                    'layer': layer,
                    'text': e.dxf.text,
                    'position': {'x': e.dxf.insert.x, 'y': e.dxf.insert.y},
                    'height': e.dxf.height
                })
            elif e.dxftype() == 'ARC':
                entities.append({
                    'type': 'arc',
                    'layer': layer,
                    'center': {'x': e.dxf.center.x, 'y': e.dxf.center.y},
                    'radius': e.dxf.radius,
                    'start_angle': e.dxf.start_angle,
                    'end_angle': e.dxf.end_angle
                })

        return JSONResponse(content={'entities': entities})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})