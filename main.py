import ezdxf
from ezdxf import units
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import io
import traceback

app = FastAPI()

# --- CORS middleware (allow all origins for development) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------
# Helper – safely get a nested key from a dict
# ----------------------------------------
def _get(obj, *keys, default=None):
    try:
        for k in keys:
            obj = obj[k]
        return obj
    except (KeyError, TypeError):
        return default

# ----------------------------------------
# DXF generation
# ----------------------------------------
def generate_dxf(data: dict) -> io.BytesIO:
    doc = ezdxf.new(dxfversion='R2010')
    msp = doc.modelspace()
    doc.header['$INSUNITS'] = units.MM

    # Layers
    doc.layers.new('S-GRID', dxfattribs={'color': 1})   # red
    doc.layers.new('S-FNDN-FTG', dxfattribs={'color': 4}) # cyan
    doc.layers.new('S-REBAR-BOT', dxfattribs={'color': 2}) # yellow
    doc.layers.new('S-ANNO-TEXT', dxfattribs={'color': 7}) # white

    # --- Raft boundary ---
    raft_boundary = _get(data, 'foundation_geometry', 'raft_boundary', default=[])
    if raft_boundary:
        pts = [(p['x'], p['y']) for p in raft_boundary]
        msp.add_lwpolyline(pts, close=True, dxfattribs={'layer': 'S-FNDN-FTG'})

    # --- Grid lines ---
    grid = _get(data, 'grid_lines', default={})
    max_y = max((p['y'] for p in raft_boundary), default=10000)
    for x_axis in grid.get('x_axis', []):
        x = x_axis.get('x_coordinate', 0)
        label = x_axis.get('label', '')
        msp.add_line((x, 0), (x, max_y), dxfattribs={'layer': 'S-GRID'})
        msp.add_text(f'({label})', dxfattribs={'layer': 'S-ANNO-TEXT'}).set_placement((x, -200))

    max_x = max((p['x'] for p in raft_boundary), default=10000)
    for y_axis in grid.get('y_axis', []):
        y = y_axis.get('y_coordinate', 0)
        label = y_axis.get('label', '')
        msp.add_line((0, y), (max_x, y), dxfattribs={'layer': 'S-GRID'})
        msp.add_text(f'({label})', dxfattribs={'layer': 'S-ANNO-TEXT'}).set_placement((-500, y))

    # --- Columns & piles ---
    elements = _get(data, 'structural_elements', default=[])
    for elem in elements:
        coords = elem.get('coordinates', {})
        x = coords.get('x', 0)
        y = coords.get('y', 0)
        dims = elem.get('dimensions', {})
        w = dims.get('width', 400)
        d = dims.get('depth', 600)
        msp.add_lwpolyline(
            [(x - w/2, y - d/2), (x + w/2, y - d/2), (x + w/2, y + d/2), (x - w/2, y + d/2)],
            close=True, dxfattribs={'layer': 'S-FNDN-FTG'}
        )
        pile = elem.get('underlying_pile', {})
        pile_dia = pile.get('diameter', 600)
        msp.add_circle((x, y), pile_dia/2, dxfattribs={'layer': 'S-FNDN-FTG'})
        eid = elem.get('element_id', 'C?')
        msp.add_text(eid, dxfattribs={'layer': 'S-ANNO-TEXT'}).set_placement((x, y + 300))

    # --- Reinforcement notes ---
    reinf = _get(data, 'reinforcement_matrix', default={})
    bottom = reinf.get('bottom_mesh', {})
    top = reinf.get('top_mesh', {})
    notes = [
        f"Bottom: {bottom.get('main_bars','?')}",
        f"Top: {top.get('main_bars','?')}"
    ]
    y_pos = _get(data, 'foundation_geometry', 'raft_boundary', 0, 'y', default=-2000) - 1000
    for note in notes:
        msp.add_text(note, dxfattribs={'layer': 'S-ANNO-TEXT'}).set_placement((0, y_pos))
        y_pos -= 400

    buf = io.BytesIO()
    doc.write(buf)
    buf.seek(0)
    return buf

# ----------------------------------------
# Endpoint – with proper error handling
# ----------------------------------------
@app.post("/generate-dxf")
async def generate_dxf_endpoint(request: Request):
    try:
        data = await request.json()
        buf = generate_dxf(data)
        return StreamingResponse(
            buf,
            media_type="application/dxf",
            headers={"Content-Disposition": "attachment; filename=layout.dxf"}
        )
    except Exception as e:
        # Print full traceback to Railway logs
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e)}
        )