-- rosettex LuaTeX read guard (spec §9.5)
-- Restricts TeX-level file reads to the build directory, TeX distribution trees,
-- and ~/.rosettex/fonts, resolving symlinks via lfs without mutating cwd.

local function normalize_sep(p)
  return (p:gsub("\\", "/"))
end

local function is_abs(p)
  return p:sub(1, 1) == "/" or p:match("^[A-Za-z]:/") ~= nil
end

local function expand_home(p)
  if p:sub(1, 1) == "~" and (p == "~" or p:sub(2, 2) == "/" or p:sub(2, 2) == "\\") then
    local home = os.getenv("HOME") or os.getenv("USERPROFILE")
    if home and home ~= "" then
      return normalize_sep(home) .. p:sub(2)
    end
  end
  return p
end

local function strip_trailing_slash(p)
  while #p > 1 and p:sub(-1) == "/" do
    p = p:sub(1, -2)
  end
  return p
end

local function realpath(p, depth)
  depth = depth or 0
  if depth > 40 then
    return nil
  end
  p = normalize_sep(expand_home(p))
  if not is_abs(p) then
    local cwd = lfs.currentdir()
    if not cwd then
      return nil
    end
    p = normalize_sep(cwd) .. "/" .. p
  end

  local prefix = ""
  local rest = p
  local drv = p:match("^([A-Za-z]:)")
  if drv then
    prefix = drv
    rest = p:sub(3)
  end

  local comps = {}
  for comp in rest:gmatch("[^/]+") do
    comps[#comps + 1] = comp
  end

  local parts = {}
  for i = 1, #comps do
    local comp = comps[i]
    if comp == "." then
      -- no-op
    elseif comp == ".." then
      if #parts > 0 then
        parts[#parts] = nil
      end
    else
      parts[#parts + 1] = comp
      local cur = prefix .. "/" .. table.concat(parts, "/")
      local sa = lfs.symlinkattributes(cur)
      if sa and sa.mode == "link" then
        local target = lfs.readlink(cur)
        if not target then
          return nil
        end
        target = normalize_sep(target)
        if not is_abs(target) then
          parts[#parts] = nil
          local parent = prefix .. "/" .. table.concat(parts, "/")
          target = parent .. "/" .. target
        end
        if i < #comps then
          local tail = {}
          for j = i + 1, #comps do
            tail[#tail + 1] = comps[j]
          end
          target = target .. "/" .. table.concat(tail, "/")
        end
        return realpath(target, depth + 1)
      end
    end
  end

  local resolved = prefix .. "/" .. table.concat(parts, "/")
  return strip_trailing_slash(resolved)
end

local allowed_roots = {}
local seen_roots = {}

local function add_root(raw)
  if not raw or raw == "" then
    return
  end
  local r = realpath(raw)
  if r and r ~= "" and not seen_roots[r] then
    seen_roots[r] = true
    allowed_roots[#allowed_roots + 1] = r
  end
end

-- 1. Build directory (cwd)
add_root(lfs.currentdir())

-- 2. TeX distribution trees
for _, var in ipairs({
  "SELFAUTOPARENT",
  "TEXMFDIST",
  "TEXMFLOCAL",
  "TEXMFHOME",
  "TEXMFVAR",
  "TEXMFSYSVAR",
  "TEXMFCONFIG",
  "TEXMFSYSCONFIG",
}) do
  local val = kpse.var_value(var)
  if val and val ~= "" then
    add_root(val)
  end
end

-- 3. ~/.rosettex/fonts (or $ROSETTEX_HOME/fonts)
local rx_home = os.getenv("ROSETTEX_HOME")
if rx_home and rx_home ~= "" then
  add_root(rx_home .. "/fonts")
else
  add_root("~/.rosettex/fonts")
end

local function is_allowed(real)
  for i = 1, #allowed_roots do
    local root = allowed_roots[i]
    if real == root or real:sub(1, #root + 1) == root .. "/" then
      return true
    end
  end
  return false
end

local function check_file(asked_name, kpse_format, must_exist)
  if not asked_name or asked_name == "" then
    return nil
  end
  local found = kpse.find_file(asked_name, kpse_format, must_exist)
  if not found then
    local lit = normalize_sep(expand_home(asked_name))
    if lfs.isfile(lit) then
      found = lit
    end
  end
  if not found then
    return nil
  end
  local real = realpath(found)
  if real and is_allowed(real) then
    return found
  end
  texio.write_nl("rosettex-guard: refused " .. tostring(asked_name) .. "\n")
  return nil
end

local function register(cb, desc, fn)
  if luatexbase and luatexbase.add_to_callback then
    luatexbase.add_to_callback(cb, fn, desc)
  else
    callback.register(cb, fn)
  end
end

register("find_read_file", "rosettex.guard.find_read_file", function(id_number, asked_name)
  return check_file(asked_name, "tex")
end)

register("find_image_file", "rosettex.guard.find_image_file", function(asked_name)
  return check_file(asked_name, "tex")
end)

register("find_data_file", "rosettex.guard.find_data_file", function(asked_name)
  return check_file(asked_name, "tex")
end)

-- Also guard LaTeX3's expl3.lua file primitives (used by \IfFileExists, \file_get_hex_dump:n,
-- \file_size:n, \file_timestamp:n, \file_mdfive_hash:n), which bypass find_read_file via Lua.
if token and token.create and lua and lua.get_functions_table then
  local funcs = lua.get_functions_table()
  local undef_id = token.command_id and token.command_id("undefined_cs")

  local function wrap_l3(csname, fn)
    local tok = token.create(csname)
    if tok and tok.command ~= undef_id then
      local id = tok.index or tok.mode
      if id and funcs[id] then
        funcs[id] = fn
      end
    end
  end

  local function to_hex(str)
    return (str:gsub(".", function(ch)
      return string.format("%02X", string.byte(ch))
    end))
  end

  wrap_l3("tex_filesize:D", function()
    local file = check_file(token.scan_string(), "tex", true)
    if file then
      local size = lfs.attributes(file, "size")
      if size then
        tex.write(size)
      end
    end
  end)

  wrap_l3("tex_filemoddate:D", function()
    local file = check_file(token.scan_string(), "tex", true)
    if file then
      local mtime = lfs.attributes(file, "modification")
      if mtime then
        local z = os.date("%z", mtime)
        local s = os.date("D:%Y%m%d%H%M%S", mtime)
        if z and z:match("^[+-]%d%d%d%d$") then
          if z == "+0000" or z == "-0000" then
            s = s .. "Z"
          else
            s = s .. z:sub(1, 3) .. "'" .. z:sub(4, 5) .. "'"
          end
        else
          s = s .. "Z"
        end
        tex.write(s)
      end
    end
  end)

  wrap_l3("tex_filedump:D", function()
    local offset = token.scan_keyword("offset") and token.scan_int() or nil
    local length = token.scan_keyword("length") and token.scan_int()
      or not token.scan_keyword("whole") and 0
      or nil
    local file = check_file(token.scan_string(), "tex", true)
    if file then
      local f = io.open(file, "rb")
      if f then
        if offset and offset > 0 then
          f:seek("set", offset)
        end
        local data = f:read(length or "a")
        f:close()
        if data then
          tex.write(to_hex(data))
        end
      end
    end
  end)

  wrap_l3("tex_mdfivesum:D", function()
    local hash
    if token.scan_keyword("file") then
      local file = check_file(token.scan_string(), "tex", true)
      if file then
        local f = io.open(file, "rb")
        if f then
          local data = f:read("*a")
          f:close()
          hash = md5 and md5.sumhexa and md5.sumhexa(data):upper() or to_hex(md5.sum(data))
        end
      end
    else
      local s = token.scan_string()
      hash = md5 and md5.sumhexa and md5.sumhexa(s):upper() or to_hex(md5.sum(s))
    end
    if hash then
      tex.write(hash)
    end
  end)
end
