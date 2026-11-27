-- rxfit.lua: one-pass fitting and placement of translated segments (rosettex PDF route).
-- Loaded by overlay.tex; reads rxdata.lua (overlay-space geometry per segment), typesets every segment with
-- tex.runtoks into a vbox with a Lua-built \parshape, picks one scale per segment, enforces the heading rule per
-- page, places the final lines page by page and writes fit.json (overlay-space rects; Python converts).

rx = rx or {}
local D = dofile("rxdata.lua")
local SP = 65781.76 -- sp per bp
local TEXPT = 72.27 / 72 -- TeX pt per bp
local ASC, DESC = 0.8, 0.2 -- line box: baseline is ASC*pt below the box top, DESC*pt above the lowest point
local HL = node.id("hlist")
local boxnum = rx_boxnum
local floor, max, min = math.floor, math.max, math.min

rx.attr = luatexbase.new_attribute("rxlink")
rx.spans = {}
rx.line_spans = {}
rx.links = {}

local function sp(bp) return floor(bp * SP + 0.5) end

local function caps(seg, pt, lead)
  local c, real, last = {}, 0, 0
  for i, b in ipairs(seg.boxes) do
    local H = b.y1 - b.y0
    local n = 0
    if H + 1e-6 >= pt then n = floor((H - pt) / lead + 1e-6) + 1 end
    c[i] = n
    real = real + n
    if n > 0 then last = i end
  end
  if last == 0 then last = #seg.boxes; c[last] = 1 end
  return c, real, last
end

function rx.m(n)
  local s = rx.cur.snips[n]
  if not s then return end
  local sc = rx.scale
  local w, h, d = s.w * sc, s.h * sc, s.d * sc
  local bw = rx.min_bw
  if bw and w > bw and w > 0 then
    local f = bw / w
    w, h, d = bw, h * f, d * f
    if rx.final then
      rx.cur.clamped[#rx.cur.clamped + 1] = "m" .. n
    end
  end
  local inner
  if rx.final then
    inner = string.format("\\includegraphics[page=%d,width=%.4fbp,height=%.4fbp]{inline_math.pdf}", s.page, w, h)
  else
    inner = string.format("\\rule{%.4fbp}{%.4fbp}", w, h)
  end
  local body = string.format("\\rxsmash{\\raisebox{%.4fbp}{%s}}", -d, inner)
  if s.link ~= nil then
    body = string.format("\\begingroup\\directlua{tex.attribute[rx.attr]=%d}%s\\endgroup", s.link, body)
  end
  tex.sprint(body)
end

local function align_skips(seg)
  local a = seg.align
  if D.rtl then
    if a == "left" then a = "right" elseif a == "right" then a = "left" end
  end
  if a == "left" then return "\\leftskip=0pt \\rightskip=0pt plus 1fil "
  elseif a == "right" then return "\\leftskip=0pt plus 1fil \\rightskip=0pt "
  elseif a == "center" then return "\\leftskip=0pt plus 1fil \\rightskip=0pt plus 1fil "
  end
  return "\\leftskip=0pt \\rightskip=0pt "
end

-- typeset seg at scale into box rxbox; returns number of lines
local function typeset(seg, scale, final)
  rx.cur, rx.scale, rx.final = seg, scale, final
  if final then seg.clamped = {} end
  local min_bw = math.huge
  for _, b in ipairs(seg.boxes) do min_bw = min(min_bw, b.x1 - b.x0) end
  rx.min_bw = min_bw
  local tol = 2000
  for _, s in pairs(seg.snips) do
    if s.w * scale > 0.25 * min_bw then tol = 10000; break end
  end
  local pt, lead = seg.size * scale, seg.lead * scale
  local c = caps(seg, pt, lead)
  local ps, np = {}, 0
  local lastw
  local hang = (not D.rtl and seg.hang and seg.hang > 0) and sp(seg.hang) or 0
  for i, b in ipairs(seg.boxes) do
    for _ = 1, c[i] do
      np = np + 1
      lastw = sp(b.x1 - b.x0)
      if np > 1 and hang > 0 and lastw > hang + sp(20) then
        ps[np] = hang .. "sp " .. (lastw - hang) .. "sp"
      else
        ps[np] = "0sp " .. lastw .. "sp"
      end
    end
  end
  local style = ""
  if seg.bold then style = style .. "\\bfseries " end
  if seg.italic then style = style .. "\\itshape " end
  if seg.color then style = style .. string.format("\\color[RGB]{%d,%d,%d} ", seg.color[1], seg.color[2], seg.color[3]) end
  local tok = string.format(
    "\\setbox%d\\vbox{\\hsize=%dsp \\parindent=0pt \\parskip=0pt \\baselineskip=%dsp \\lineskiplimit=-\\maxdimen "
      .. "\\lineskip=0pt \\splittopskip=0pt \\splitmaxdepth=\\maxdimen \\emergencystretch=3em \\tolerance=%d "
      .. "\\hbadness=10000 \\fontsize{%.3f}{%.3f}\\selectfont %s%s\\parshape %d %s \\noindent"
      .. "\\csname rxb@%s\\endcsname\\par}",
    boxnum, lastw or sp(100), sp(lead), tol, pt * TEXPT, lead * TEXPT, style, align_skips(seg), np, table.concat(ps, " "), seg.id)
  tex.runtoks(function() tex.sprint(tok) end)
  local v = tex.box[boxnum]
  local n = 0
  if v and v.list then
    for _ in node.traverse_id(HL, v.list) do n = n + 1 end
  end
  rx.last = { seg, scale }
  return n
end

local function take_lines()
  local v = tex.box[boxnum]
  local out = {}
  if v and v.list then
    for l in node.traverse_id(HL, v.list) do out[#out + 1] = node.copy(l) end
  end
  return out
end

local function has_snips(seg) return next(seg.snips) ~= nil end

-- finalize seg at scale: lines (final nodes) + counts
local function settle(seg, scale, n)
  local c, real, last = caps(seg, seg.size * scale, seg.lead * scale)
  if has_snips(seg) or not (rx.last and rx.last[1] == seg and rx.last[2] == scale) then
    n = typeset(seg, scale, true)
  end
  local f = { scale = scale, pt = seg.size * scale, lead = seg.lead * scale, caps = c, last = last, lines = take_lines() }
  f.n = #f.lines
  f.overflow = max(0, f.n - real)
  return f
end

local function fit_seg(seg)
  local list = {}
  for _, s in ipairs(D.scales) do list[#list + 1] = s end
  if D.fallback then for _, s in ipairs(D.fscales) do list[#list + 1] = s end end
  local chosen, n
  for i, s in ipairs(list) do
    n = typeset(seg, s, false)
    local _, real = caps(seg, seg.size * s, seg.lead * s)
    chosen = s
    if n <= real then break end
  end
  seg.fit = settle(seg, chosen, n)
  seg.fit.fallback = D.fallback and (chosen < 0.8 - 1e-9 or seg.fit.overflow > 0)
end

local function flush_lines(f)
  for _, l in ipairs(f.lines) do node.flush_list(l) end
end

-- link rect recording ---------------------------------------------------------------------------------------
-- Every visible node on a link-containing line gets a latelua span (before/after); at collect_links(), spans on
-- the same line are sorted visually (handles RTL/bidi reordering) and adjacent same-link spans merge only when
-- no non-link or other-link node separates them and the gap is within 1.5*pt.
function rx.mark0(sid)
  local x, y = pdf.getpos()
  local s = rx.spans[sid]
  s.x0, s.y = x, y
end

function rx.mark1(sid)
  local x, _ = pdf.getpos()
  local s = rx.spans[sid]
  s.x1 = x
end

local function new_mark(fn, sid)
  local w = node.new("whatsit", "late_lua")
  w.data = string.format("rx.%s(%d)", fn, sid)
  return w
end

local function add_link_rect(link, pg, pt, x0, x1, y)
  if not y or x1 <= x0 then return end
  local yb = y / SP
  local key = link .. ":" .. pg
  local lk = rx.links[key]
  if not lk then lk = { link = link, page = pg, rects = {} }; rx.links[key] = lk; rx.links[#rx.links + 1] = lk end
  lk.rects[#lk.rects + 1] = { x0 / SP, yb - DESC * pt, x1 / SP, yb + ASC * pt }
end

local function collect_links()
  for _, ls in ipairs(rx.line_spans) do
    local items = {}
    for _, s in ipairs(ls.spans) do
      if s.x0 and s.x1 and s.y then
        local xa, xb = min(s.x0, s.x1), max(s.x0, s.x1)
        items[#items + 1] = { link = s.link, x0 = xa, x1 = xb, y = s.y }
      end
    end
    table.sort(items, function(a, b) return a.x0 < b.x0 end)
    local cur
    local max_gap = 1.5 * ls.pt * SP
    for _, it in ipairs(items) do
      if it.link < 0 then
        if cur then add_link_rect(cur.link, ls.page, ls.pt, cur.x0, cur.x1, cur.y); cur = nil end
      elseif cur and cur.link == it.link and (it.x0 - cur.x1) <= max_gap then
        cur.x1 = max(cur.x1, it.x1)
        cur.y = it.y
      else
        if cur then add_link_rect(cur.link, ls.page, ls.pt, cur.x0, cur.x1, cur.y) end
        cur = { link = it.link, x0 = it.x0, x1 = it.x1, y = it.y }
      end
    end
    if cur then add_link_rect(cur.link, ls.page, ls.pt, cur.x0, cur.x1, cur.y) end
  end
end

local function mark_links(line, pt, pg)
  -- only glyph/box/rule nodes carry a run: glue, penalties and dir whatsits (which carry the attribute too) are transparent
  local carrier = { [node.id("glyph")] = true, [node.id("hlist")] = true, [node.id("vlist")] = true, [node.id("rule")] = true }
  local head = line.head
  local has_link = false
  local n = head
  while n do
    if carrier[n.id] and node.has_attribute(n, rx.attr) then has_link = true; break end
    n = n.next
  end
  if not has_link then return end
  local ls = { page = pg, pt = pt, spans = {} }
  rx.line_spans[#rx.line_spans + 1] = ls
  n = head
  while n do
    local nxt = n.next
    if carrier[n.id] then
      local a = node.has_attribute(n, rx.attr)
      local sid = #rx.spans + 1
      local spn = { link = a or -1 }
      rx.spans[sid] = spn
      ls.spans[#ls.spans + 1] = spn
      head = node.insert_before(head, n, new_mark("mark0", sid))
      node.insert_after(head, n, new_mark("mark1", sid))
    end
    n = nxt
  end
  line.head = head
end

-- page assembly ---------------------------------------------------------------------------------------------
local function jstr(s) return '"' .. s:gsub('[%c"\\]', function(ch) return string.format("\\u%04x", ch:byte()) end) .. '"' end
local function jnum(x) return string.format("%.4f", x) end

local function place_all()
  local items = {}
  for pi = 1, #D.pages do items[pi] = {} end
  for _, seg in ipairs(D.segs) do
    local f = seg.fit
    seg.placed = {}
    local li = 1
    for bi, b in ipairs(seg.boxes) do
      local cnt = f.caps[bi]
      if bi > f.last then cnt = 0 end
      if bi == f.last then cnt = max(cnt, f.n - (li - 1)) end
      local placed = 0
      local lastbase
      for i = 0, cnt - 1 do
        local l = f.lines[li]
        if not l then break end
        li = li + 1
        local base = b.y1 - ASC * f.pt - i * f.lead
        mark_links(l, f.pt, b.page)
        local it = items[b.page + 1]
        it[#it + 1] = { x = b.x0, base = base, node = l }
        placed = placed + 1
        lastbase = base
      end
      local ybot = lastbase and (lastbase - DESC * f.pt) or b.y1
      seg.placed[bi] = { page = b.page, rect = { b.x0, min(ybot, b.y1), b.x1, b.y1 }, lines = placed }
    end
  end
  return items
end

local function ship_page(pi, its)
  local pg = D.pages[pi]
  local W, H = sp(pg.w), sp(pg.h)
  table.sort(its, function(a, b) return a.base > b.base end)
  local head, tail
  local function add(n)
    if tail then tail.next = n; n.prev = tail else head = n end
    tail = n
  end
  local prev = H
  for _, it in ipairs(its) do
    local by = sp(it.base)
    local k = node.new("kern"); k.kern = prev - by; add(k)
    local wrap = node.new("hlist")
    wrap.width, wrap.height, wrap.depth = 0, 0, 0
    wrap.dir = "TLT"
    local xk = node.new("kern"); xk.kern = sp(it.x)
    it.node.shift = 0 -- RTL paragraphs shift each line by (hsize - line width); the parshape already places it
    xk.next = it.node; it.node.prev = xk
    wrap.head = xk
    add(wrap)
    prev = by
  end
  tex.pagewidth, tex.pageheight = W, H
  if head then
    local vb = node.vpack(head, H, "exactly")
    vb.dir = "TLT"
    vb.width = W
    tex.box[boxnum] = vb
  else -- a page without segments still ships (overlay page count = source page count)
    tex.runtoks(function() tex.sprint(string.format("\\setbox%d\\vbox to %dsp{\\hrule width %dsp height 0pt\\vss}", boxnum, H, W)) end)
  end
  rx.curpage = pi - 1
  tex.runtoks(function() tex.sprint("\\shipout\\box" .. boxnum) end)
end

local function write_fit()
  collect_links()
  local out = { '{"segments":[' }
  for si, seg in ipairs(D.segs) do
    local f = seg.fit
    local bx = {}
    for _, p in ipairs(seg.placed) do
      bx[#bx + 1] = string.format('{"page":%d,"rect":[%s,%s,%s,%s],"lines":%d}', p.page, jnum(p.rect[1]), jnum(p.rect[2]),
        jnum(p.rect[3]), jnum(p.rect[4]), p.lines)
    end
    local cl = {}
    for _, c in ipairs(seg.clamped or {}) do cl[#cl + 1] = jstr(c) end
    out[#out + 1] = string.format(
      '%s{"id":%s,"kind":%s,"scale":%s,"pt":%s,"boxes":[%s],"overflow_lines":%d,"overflow_pt":%s,"fallback":%s,"n_lines":%d,"clamped":[%s]}',
      si > 1 and "," or "", jstr(seg.id), jstr(seg.kind), jnum(f.scale), jnum(f.pt), table.concat(bx, ","), f.overflow,
      jnum(f.overflow * f.lead), f.fallback and "true" or "false", f.n, table.concat(cl, ","))
  end
  out[#out + 1] = '],"links":['
  for i, lk in ipairs(rx.links) do
    local rs = {}
    for _, r in ipairs(lk.rects) do rs[#rs + 1] = string.format("[%s,%s,%s,%s]", jnum(r[1]), jnum(r[2]), jnum(r[3]), jnum(r[4])) end
    out[#out + 1] = string.format('%s{"link":%d,"page":%d,"rects":[%s]}', i > 1 and "," or "", lk.link, lk.page, table.concat(rs, ","))
  end
  out[#out + 1] = "]}"
  local fh = io.open("fit.json", "w")
  fh:write(table.concat(out))
  fh:close()
end

function rx.run()
  -- positions are absolute: an LTR page layer even when the document language is RTL (review-3 F3)
  tex.runtoks(function() tex.sprint("\\pagedir TLT\\relax\\bodydir TLT\\relax") end)
  for _, seg in ipairs(D.segs) do fit_seg(seg) end
  -- heading rule (R2): never below the largest final paragraph size on the page
  local pmax = {}
  for _, seg in ipairs(D.segs) do
    if seg.kind == "paragraph" then
      for _, pg in ipairs(seg.pages) do pmax[pg] = max(pmax[pg] or 0, seg.fit.pt) end
    end
  end
  for _, seg in ipairs(D.segs) do
    if seg.kind == "heading" then
      local P = pmax[seg.boxes[1].page]
      if P then
        local target = min(P, seg.size)
        if seg.fit.pt < target - 1e-6 then
          local cand
          for _, s in ipairs(D.scales) do
            if seg.size * s >= target - 1e-6 and (not cand or s < cand) then cand = s end
          end
          if cand and cand > seg.fit.scale + 1e-9 then
            local n = typeset(seg, cand, false)
            flush_lines(seg.fit)
            local fb = seg.fit.fallback
            seg.fit = settle(seg, cand, n)
            seg.fit.fallback = fb and true or seg.fit.overflow > 0
            seg.fit.raised = true
          end
        end
      end
    end
  end
  local items = place_all()
  for pi = 1, #D.pages do ship_page(pi, items[pi]) end
  write_fit()
end
