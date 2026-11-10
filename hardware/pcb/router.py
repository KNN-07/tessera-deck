"""A small two-layer grid router for the Tessera Deck boards.

It is deliberately simple: tracks run on a square grid, orthogonally, and each
net is grown as a tree by repeated shortest-path searches (A* with a turn
penalty and a via cost). When a net cannot be completed, the nets in its way
are ripped up and routed again afterwards. It is deterministic, has no
dependencies beyond numpy, and is good enough for boards this sparse. KiCad's
DRC, not this file, is the judge of the result.

How clearance is guaranteed
    Every piece of copper "claims" the grid cells in which the centre of a
    foreign track would come too close. A cell claimed by two different nets
    is closed to everyone. A net may only use free cells and its own.
    Track centres of different nets therefore stay at least three cells
    (0.45 mm) apart, which with 0.15 mm tracks leaves 0.3 mm of clearance,
    and at least four cells (0.6 mm) from a via centre, which leaves 0.225 mm.

SPDX-License-Identifier: CERN-OHL-P-2.0
"""
import heapq

import numpy as np

GRID = 0.15            # mm
TRACK = 0.15           # mm
CLEARANCE = 0.2        # mm, the design rule
VIA_DIAMETER, VIA_DRILL = 0.6, 0.3
EDGE_KEEPOUT = 0.5     # track centre to board edge (rule: 0.3 mm copper to edge)
PAD_CLAIM = CLEARANCE + TRACK / 2 + 0.03       # how far a pad's claim reaches beyond its copper
TRACK_CLAIM_CELLS = 2                          # foreign centres must be >= 3 cells away
VIA_CLAIM_CELLS = 3                            # >= 4 cells = 0.6 mm from a via centre
VIA_FREE_CELLS = 2                             # neighbourhood free of foreign tracks and vias to drop a via
NO_VIA_AROUND_PAD = 0.4                        # via centre to the edge of any pad: not in the solder
VIA_PAD_CLAIM = VIA_DIAMETER / 2 + CLEARANCE + 0.03   # via centre to a foreign pad's copper
ESCAPE_CELLS = 6                               # plain stub length beyond the pad, in cells
FANOUT_CELLS = (5, 11)                         # fan-out via distance beyond the pad: near row, far row
TURN_COST = 3
WRONG_WAY_COST = 3     # extra cost per cell against a layer's direction: front runs east-west, back north-south.
                       # Keeping to that stops bundles on the two layers from fencing a pin in.
HALO = 1.2                                     # mm around every pad where passing through costs extra,
HALO_COST = 6                                  # so tracks do not wall in other nets' pads
VIA_COST = 15
BLOCKED = -1


class Pad:
    def __init__(self, ref, number, net, cx, cy, x0, y0, x1, y1, layers, round_, escape=None, far=False):
        self.ref, self.number, self.net = ref, number, net
        self.cx, self.cy = cx, cy
        self.box = (x0, y0, x1, y1)
        self.layers = layers            # tuple of 0 (front) and/or 1 (back)
        self.round = round_
        self.escape = escape            # (dx, dy): pre-route a short stub this way
        self.far = far                  # fan-out via in the far row (pads alternate)
        self.cells = []                 # usable access cells: (layer, ix, iy)


class Router:
    def __init__(self, width, height, holes=(), corner=0.0):
        """holes: rectangles (x0, y0, x1, y1) cut out of the board."""
        self.nx = int(round(width / GRID)) + 1
        self.ny = int(round(height / GRID)) + 1
        self.claim = np.zeros((2, self.ny, self.nx), dtype=np.int32)
        self.tclaim = np.zeros((2, self.ny, self.nx), dtype=np.int32)   # the same without pads: edges, tracks, vias
        self.vclaim = np.zeros((self.ny, self.nx), dtype=np.int32)      # where a via would be too close to a pad
        self.no_via = np.zeros((self.ny, self.nx), dtype=bool)
        self.halo = np.zeros((2, self.ny, self.nx), dtype=np.uint8)
        self.pads = []
        self.net_ids = {}
        self.routed = {}                # net -> dict(segments, vias, claims)
        self.failed = []
        self.verbose = False
        self.owner = np.zeros((2, self.ny, self.nx), dtype=np.int32)   # whose track claims a cell
        self.rips = {}                  # net -> how often it has been ripped up

        k = EDGE_KEEPOUT
        for layer in (0, 1):
            self._claim(layer, -1, -1, k, height + 1, BLOCKED)
            self._claim(layer, width - k, -1, width + 1, height + 1, BLOCKED)
            self._claim(layer, -1, -1, width + 1, k, BLOCKED)
            self._claim(layer, -1, height - k, width + 1, height + 1, BLOCKED)
            for cx, cy in ((0, 0), (width, 0), (0, height), (width, height)):    # rounded corners
                self._claim(layer, cx - corner, cy - corner, cx + corner, cy + corner, BLOCKED)
            for x0, y0, x1, y1 in holes:
                self._claim(layer, x0 - k, y0 - k, x1 + k, y1 + k, BLOCKED)

    # ---- grid helpers ----

    def _range(self, lo, hi, n):
        """Indices of the cells whose centres lie in [lo, hi]."""
        a = int(np.ceil(lo / GRID - 1e-9))
        b = int(np.floor(hi / GRID + 1e-9))
        return max(a, 0), min(b, n - 1)

    def _claim(self, layer, x0, y0, x1, y1, net, grid=None):
        ix0, ix1 = self._range(x0, x1, self.nx)
        iy0, iy1 = self._range(y0, y1, self.ny)
        if ix0 > ix1 or iy0 > iy1:
            return
        for g in ([self.claim[layer], self.tclaim[layer]] if grid is None else [grid]):
            sub = g[iy0:iy1 + 1, ix0:ix1 + 1]
            if net == BLOCKED:
                sub[:] = BLOCKED
            else:
                sub[(sub != 0) & (sub != net)] = BLOCKED
                sub[sub == 0] = net

    def _claim_cells(self, layer, ix, iy, k, net):
        for g in (self.claim, self.tclaim):
            sub = g[layer, max(iy - k, 0):iy + k + 1, max(ix - k, 0):ix + k + 1]
            sub[(sub != 0) & (sub != net)] = BLOCKED
            sub[sub == 0] = net

    # ---- input ----

    def add_pad(self, ref, number, net, cx, cy, x0, y0, x1, y1, layers, round_=False, escape=None, far=False):
        net_id = BLOCKED
        if net:
            net_id = self.net_ids.setdefault(net, len(self.net_ids) + 1)
        pad = Pad(ref, number, net, cx, cy, x0, y0, x1, y1, layers, round_, escape, far)
        self.pads.append(pad)
        for layer in layers:
            self._claim(layer, x0 - PAD_CLAIM, y0 - PAD_CLAIM, x1 + PAD_CLAIM, y1 + PAD_CLAIM, net_id, self.claim[layer])
        v = VIA_PAD_CLAIM
        self._claim(0, x0 - v, y0 - v, x1 + v, y1 + v, net_id, self.vclaim)
        if net:
            hx0, hx1 = self._range(x0 - HALO, x1 + HALO, self.nx)
            hy0, hy1 = self._range(y0 - HALO, y1 + HALO, self.ny)
            for layer in layers:
                self.halo[layer, hy0:hy1 + 1, hx0:hx1 + 1] = HALO_COST
        m = NO_VIA_AROUND_PAD
        ix0, ix1 = self._range(x0 - m, x1 + m, self.nx)
        iy0, iy1 = self._range(y0 - m, y1 + m, self.ny)
        self.no_via[iy0:iy1 + 1, ix0:ix1 + 1] = True

    def _access_cells(self):
        for pad in self.pads:
            if not pad.net:
                continue
            net_id = self.net_ids[pad.net]
            x0, y0, x1, y1 = pad.box
            ix0, ix1 = self._range(x0 + 0.04, x1 - 0.04, self.nx)
            iy0, iy1 = self._range(y0 + 0.04, y1 - 0.04, self.ny)
            r2 = ((x1 - x0) / 2 - 0.04) ** 2
            pad.cells = []
            for layer in pad.layers:
                for iy in range(iy0, iy1 + 1):
                    for ix in range(ix0, ix1 + 1):
                        if pad.round and (ix * GRID - pad.cx) ** 2 + (iy * GRID - pad.cy) ** 2 > r2:
                            continue
                        if self.claim[layer, iy, ix] == net_id:
                            pad.cells.append((layer, ix, iy))
            assert pad.cells, f"pad {pad.ref}.{pad.number} ({pad.net}) has no reachable cell"

    def _escapes(self):
        """Fine-pitch pads get a straight stub out of the pad row before anything else is
        routed, so that a neighbour's track can never turn across their only way out.
        Where there is room the stub ends in a via, alternately near and far, which
        makes every such pin reachable from the back of the board as well."""
        self.fanout = {}                       # (ix, iy) -> net of a fan-out via
        for pad in self.pads:
            if not pad.net or not pad.escape:
                continue
            net_id = self.net_ids[pad.net]
            dx, dy = pad.escape
            layer = pad.layers[0]

            def key(c):                        # the access cell nearest the pad's centre line
                _, ix, iy = c
                along = (ix * GRID - pad.cx) * dx + (iy * GRID - pad.cy) * dy
                across = abs((ix * GRID - pad.cx) * dy) + abs((iy * GRID - pad.cy) * dx)
                return (round(across / GRID), -along)
            _, ix, iy = min((c for c in pad.cells if c[0] == layer), key=key)
            x0, y0, x1, y1 = pad.box
            cells, beyond_at = [(ix, iy)], [0]
            want = FANOUT_CELLS[1 if pad.far else 0]
            while beyond_at[-1] < max(want, ESCAPE_CELLS):
                nx_, ny_ = ix + dx, iy + dy
                if not (0 <= nx_ < self.nx and 0 <= ny_ < self.ny) or self.claim[layer, ny_, nx_] not in (0, net_id):
                    break
                ix, iy = nx_, ny_
                cells.append((ix, iy))
                beyond_at.append(beyond_at[-1] + (0 if x0 <= ix * GRID <= x1 and y0 <= iy * GRID <= y1 else 1))
            via = None
            if beyond_at[-1] >= want and len(pad.layers) == 1:
                vx, vy = cells[beyond_at.index(want)]
                if self._fanout_fits(pad, vx, vy):
                    via = (vx, vy)
                    cells = cells[:beyond_at.index(want) + 1]
            if via is None:
                keep = max(i for i, b in enumerate(beyond_at) if b <= ESCAPE_CELLS)
                cells = cells[:keep + 1]
            if len(cells) > 1:
                pad.stub_cells, pad.stub_layer, pad.stub_via = cells, layer, via
                if via:
                    self.fanout[via] = pad.net
        for pad in self.pads:                  # claim only after every stub exists
            cells = getattr(pad, "stub_cells", None)
            if not cells:
                continue
            net_id = self.net_ids[pad.net]
            x0, y0, x1, y1 = pad.box
            for ix, iy in cells:
                self._claim_cells(pad.stub_layer, ix, iy, TRACK_CLAIM_CELLS, net_id)
                if not (x0 <= ix * GRID <= x1 and y0 <= iy * GRID <= y1):
                    pad.cells.append((pad.stub_layer, ix, iy))
            if pad.stub_via:
                vx, vy = pad.stub_via
                h = int(round(HALO / GRID))
                for layer in (0, 1):
                    self._claim_cells(layer, vx, vy, VIA_CLAIM_CELLS, net_id)
                    # tracks should cross a row of fan-out vias, not run along it
                    self.halo[layer, max(vy - h, 0):vy + h + 1, max(vx - h, 0):vx + h + 1] = HALO_COST
                pad.cells.append((1 - pad.stub_layer, vx, vy))

    def _fanout_fits(self, pad, vx, vy):
        """Exact geometry for a fan-out via: pads, other fan-out vias and the board edge."""
        x, y = vx * GRID, vy * GRID
        net_id = self.net_ids[pad.net]
        other = 1 - pad.layers[0]
        near = self.claim[other, max(vy - 1, 0):vy + 2, max(vx - 1, 0):vx + 2]
        if not np.all((near == 0) | (near == net_id)):
            return False
        for q in self.pads:
            if q is pad:
                continue
            x0, y0, x1, y1 = q.box
            d = np.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1))
            if d < (VIA_DIAMETER / 2 + (0.1 if q.net == pad.net else CLEARANCE + 0.02)):
                return False
        for (ox, oy), net in self.fanout.items():
            d = np.hypot(ox * GRID - x, oy * GRID - y)
            if d < (VIA_DIAMETER + (0.25 if net == pad.net else CLEARANCE + 0.05)):
                return False
        return True

    # ---- routing ----

    def _stub_use(self, pad):
        """(cells of the stub a route uses, whether its fan-out via is used)."""
        plane = self.nx * self.ny
        used = self.routed[pad.net]["cells"]
        cells = pad.stub_cells
        via_used = bool(pad.stub_via) and (1 - pad.stub_layer) * plane + pad.stub_via[1] * self.nx + pad.stub_via[0] in used
        reach = max((i for i, (ix, iy) in enumerate(cells) if pad.stub_layer * plane + iy * self.nx + ix in used), default=0)
        if via_used:
            reach = len(cells) - 1
        return cells[:reach + 1], via_used

    @property
    def segments(self):
        """All tracks. Escape stubs are cut back to the part a route actually uses."""
        out = []
        for pad in self.pads:
            if not getattr(pad, "stub_cells", None) or pad.net not in self.routed:
                continue
            cells, _ = self._stub_use(pad)
            if len(cells) < 2:
                continue
            (sx, sy), (ex, ey) = cells[0], cells[-1]
            out.append((pad.net, pad.stub_layer, pad.cx, pad.cy, sx * GRID, sy * GRID))
            out.append((pad.net, pad.stub_layer, sx * GRID, sy * GRID, ex * GRID, ey * GRID))
        return out + [seg for net in sorted(self.routed) for seg in self.routed[net]["segments"]]

    @property
    def vias(self):
        out = [via for net in sorted(self.routed) for via in self.routed[net]["vias"]]
        for pad in self.pads:
            if getattr(pad, "stub_via", None) and pad.net in self.routed and self._stub_use(pad)[1]:
                out.append((pad.net, pad.stub_via[0] * GRID, pad.stub_via[1] * GRID))
        return out

    def route(self, order=None, max_rips=400):
        self._access_cells()
        self._escapes()
        self.base = self.claim.copy()          # edges, pads and stubs: never ripped up
        self.tbase = self.tclaim.copy()
        self.halo_bytes = self.halo.reshape(-1).tobytes()
        queue = list(order or sorted(self.net_ids))
        rips = 0
        while queue:
            net = queue.pop(0)
            only = None
            if isinstance(net, tuple):         # (net, predicate): connect just these pads for now
                net, only = net
            while True:
                stuck = self._route_net(net, only)
                if self.verbose:
                    print(f"      {net}: {'ok' if stuck is None else 'stuck'} ({len(queue)} nets to go, {rips} rip-ups)", flush=True)
                if stuck is None:
                    break
                blockers = self._blockers(net, stuck)
                if not blockers or rips >= max_rips:
                    self.failed.append((net, stuck))
                    break
                for b in blockers:
                    rips += 1
                    self.rips[b] = self.rips.get(b, 0) + 1
                    del self.routed[b]
                    queue.append(b)
                self.routed.pop(net, None)
                self._rebuild()
        self.rip_count = rips
        return not self.failed

    def _rebuild(self):
        self.claim = self.base.copy()
        self.tclaim = self.tbase.copy()
        self.owner[:] = 0
        for net, data in self.routed.items():
            net_id = self.net_ids[net]
            for layer, ix, iy, k in data["claims"]:
                self._claim_cells(layer, ix, iy, k, net_id)
                self.owner[layer, max(iy - k, 0):iy + k + 1, max(ix - k, 0):ix + k + 1] = net_id

    def _via_ok(self, net_id, tclaim=None):
        """Cells where this net may drop a via: clear of foreign pads by exact distance, not
        in any pad's solder, and with no foreign track or via claim in the neighbourhood."""
        t = self.tclaim if tclaim is None else tclaim
        free = (t == 0) | (t == net_id)
        out = free[0] & free[1]
        k = VIA_FREE_CELLS
        for axis in (0, 1):
            acc = out.copy()
            for shift in range(1, k + 1):
                a = np.zeros_like(out)
                b = np.zeros_like(out)
                if axis == 0:
                    a[shift:, :] = out[:-shift, :]
                    b[:-shift, :] = out[shift:, :]
                else:
                    a[:, shift:] = out[:, :-shift]
                    b[:, :-shift] = out[:, shift:]
                acc &= a & b
            out = acc
        return out & ~self.no_via & ((self.vclaim == 0) | (self.vclaim == net_id))

    def _net_setup(self, net):
        pads = [p for p in self.pads if p.net == net]
        nx, plane = self.nx, self.nx * self.ny
        cell_pad = {}
        for i, p in enumerate(pads):
            for (layer, ix, iy) in p.cells:
                cell_pad[layer * plane + iy * nx + ix] = i
        return pads, cell_pad

    def _route_net(self, net, only=None):
        """Routes a net, or continues it. Returns None on success, else (tree, targets, goal)
        of the step that got stuck. `only` limits this call to the pads it accepts."""
        net_id = self.net_ids[net]
        pads, cell_pad = self._net_setup(net)
        nx, plane = self.nx, self.nx * self.ny
        wanted = [i for i, p in enumerate(pads) if only is None or only(p)]
        if not wanted:
            return None
        if net in self.routed:
            data = self.routed[net]
            connected, tree = data["connected"], data["tree"]
        else:
            # start from the pad nearest the centroid, which keeps trees compact
            mx = sum(pads[i].cx for i in wanted) / len(wanted)
            my = sum(pads[i].cy for i in wanted) / len(wanted)
            first = min(wanted, key=lambda i: (pads[i].cx - mx) ** 2 + (pads[i].cy - my) ** 2)
            connected = [first]
            tree = {c for c, i in cell_pad.items() if i == first}
            data = self.routed[net] = dict(segments=[], vias=[], claims=[], cells=set(), connected=connected, tree=tree)

        own_vias = {iy * nx + ix for (ix, iy), n in self.fanout.items() if n == net}
        while True:
            rest = [i for i in wanted if i not in connected]
            if not rest:
                break
            usable_np = (self.claim == 0) | (self.claim == net_id)
            usable = usable_np.reshape(-1).tobytes()
            via_ok = self._via_ok(net_id).reshape(-1).tobytes()
            goal = min(rest, key=lambda i: min(abs(pads[i].cx - pads[j].cx) + abs(pads[i].cy - pads[j].cy)
                                                for j in connected))
            targets = {c: i for c, i in cell_pad.items() if i in rest}
            path = self._search(tree, targets, usable, via_ok, self.halo_bytes, pads[goal].box, own_vias)
            if path is None:
                return tree, targets, pads[goal].box
            hit = targets[path[-1]]
            connected.append(hit)
            self._commit(net, net_id, path, pads, cell_pad, data)
            tree.update(path)
            tree.update(c for c, i in cell_pad.items() if i == hit)
        return None

    def _blockers(self, net, stuck):
        """Which routed nets stand between this net's tree and its next pad."""
        tree, targets, goal = stuck
        net_id = self.net_ids[net]
        static = (self.base == 0) | (self.base == net_id)
        usable = (self.claim == 0) | (self.claim == net_id)
        table = np.zeros(len(self.net_ids) + 2, dtype=np.int64)
        for name, i in self.net_ids.items():
            table[i] = min(60 * (1 + self.rips.get(name, 0)), 250)
        penalty = np.where(usable, self.halo, np.minimum(table[self.owner] + self.halo, 255)).astype(np.uint8)
        path = self._search(tree, targets, static.reshape(-1).tobytes(), self._via_ok(net_id, self.tbase).reshape(-1).tobytes(),
                            penalty.reshape(-1).tobytes(), goal)
        if path is None:
            return []
        flat_owner = self.owner.reshape(-1)
        flat_usable = usable.reshape(-1)
        names = {i: name for name, i in self.net_ids.items()}
        found = []
        nx, plane = self.nx, self.nx * self.ny
        for c in path:
            layer, rest = divmod(c, plane)
            ix, iy = rest % nx, rest // nx
            k = VIA_FREE_CELLS
            for other in (0, 1):            # a via needs both layers clear around it
                if other != layer and not (c + (other - layer) * plane in path):
                    continue
                window = self.owner[other, max(iy - (k if other != layer else 0), 0):iy + (k if other != layer else 0) + 1,
                                    max(ix - (k if other != layer else 0), 0):ix + (k if other != layer else 0) + 1]
                if other != layer or not flat_usable[c]:
                    for o in np.unique(window):
                        if o and o != net_id and names[int(o)] in self.routed and names[int(o)] not in found:
                            found.append(names[int(o)])
        return [n for n in found if n != net]

    def _search(self, sources, targets, usable, via_ok, extra, goal, own_vias=()):
        nx, plane = self.nx, self.nx * self.ny
        gx0, gx1 = self._range(goal[0], goal[2], self.nx)
        gy0, gy1 = self._range(goal[1], goal[3], self.ny)
        steps = (1, -1, nx, -nx)
        dist = {}
        prev = {}
        heap = []

        def h(rest):
            x, y = rest % nx, rest // nx
            return (gx0 - x if x < gx0 else x - gx1 if x > gx1 else 0) + (gy0 - y if y < gy0 else y - gy1 if y > gy1 else 0)

        for c in sources:
            s = c * 5 + 4
            dist[s] = 0
            heap.append((h(c % plane), 0, s))
        heapq.heapify(heap)
        while heap:
            _, d, s = heapq.heappop(heap)
            if dist.get(s, 1 << 60) < d:
                continue
            c, direction = divmod(s, 5)
            if c in targets:
                path = [c]
                while s in prev:
                    s = prev[s]
                    if s // 5 != path[-1]:
                        path.append(s // 5)
                path.reverse()
                return path
            layer, rest = divmod(c, plane)
            x = rest % nx
            for nd in range(4):
                if nd == 0 and x == nx - 1 or nd == 1 and x == 0:
                    continue
                n = c + steps[nd]
                if n < layer * plane or n >= (layer + 1) * plane or not usable[n]:
                    continue
                cost = d + 1 + extra[n] + (TURN_COST if direction != nd and direction != 4 else 0) \
                    + (WRONG_WAY_COST if (nd >= 2) != (layer == 1) else 0)
                ns = n * 5 + nd
                if cost < dist.get(ns, 1 << 60):
                    dist[ns] = cost
                    prev[ns] = s
                    heapq.heappush(heap, (cost + h(n % plane), cost, ns))
            if via_ok[rest] or rest in own_vias:
                n = rest + (1 - layer) * plane
                ns = n * 5 + 4
                cost = d + VIA_COST + extra[n]
                if cost < dist.get(ns, 1 << 60):
                    dist[ns] = cost
                    prev[ns] = s
                    heapq.heappush(heap, (cost + h(rest), cost, ns))
        return None

    @staticmethod
    def _inside(pad, cell):
        x0, y0, x1, y1 = pad.box
        return x0 <= cell[1] * GRID <= x1 and y0 <= cell[2] * GRID <= y1

    def _commit(self, net, net_id, path, pads, cell_pad, data):
        nx, plane = self.nx, self.nx * self.ny

        def unpack(c):
            layer, rest = divmod(c, plane)
            return layer, rest % nx, rest // nx

        def claim(layer, ix, iy, k):
            self._claim_cells(layer, ix, iy, k, net_id)
            self.owner[layer, max(iy - k, 0):iy + k + 1, max(ix - k, 0):ix + k + 1] = net_id
            data["claims"].append((layer, ix, iy, k))

        data["cells"].update(path)
        cells = [unpack(c) for c in path]
        for (layer, ix, iy) in cells:
            claim(layer, ix, iy, TRACK_CLAIM_CELLS)

        # split into per-layer runs at vias, then merge straight runs into segments
        runs, run = [], [cells[0]]
        for a, b in zip(cells, cells[1:]):
            if a[0] != b[0]:
                runs.append(run)
                if (a[1], a[2]) not in self.fanout:          # a fan-out via is already there
                    data["vias"].append((net, a[1] * GRID, a[2] * GRID))
                    for layer in (0, 1):
                        claim(layer, a[1], a[2], VIA_CLAIM_CELLS)
                run = [b]
            else:
                run.append(b)
        runs.append(run)
        for run in runs:
            layer = run[0][0]
            pts = [(ix * GRID, iy * GRID) for (_, ix, iy) in run]
            # a run that starts or ends on a pad is extended to the pad's centre
            for end, c in ((0, path[0]), (-1, path[-1])):
                cell = cells[0] if end == 0 else cells[-1]
                if run[end] == cell and c in cell_pad and self._inside(pads[cell_pad[c]], cell):
                    p = pads[cell_pad[c]]
                    pts.insert(0, (p.cx, p.cy)) if end == 0 else pts.append((p.cx, p.cy))
            start = pts[0]
            for i in range(1, len(pts)):
                last = i == len(pts) - 1
                if not last:
                    ax, ay = pts[i][0] - start[0], pts[i][1] - start[1]
                    bx, by = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
                    if abs(ax * by - ay * bx) < 1e-9 and ax * bx + ay * by > 0:
                        continue            # still going straight
                if pts[i] != start:
                    data["segments"].append((net, layer, start[0], start[1], pts[i][0], pts[i][1]))
                start = pts[i]
