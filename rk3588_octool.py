import curses
import mmap
import os
import struct
import time
import textwrap

# Common Constants
REG_SIZE = 0x1000

CRU_BASE            = 0xFD7C0000
CRU_BIGCORE0_BASE   = 0xFD810000
CRU_BIGCORE1_BASE   = 0xFD812000
CRU_DSU_BASE        = 0xFD818000
CRU_DDRPHY0_BASE    = 0xFD800000 # reading devmem locks device
CRU_DDRPHY1_BASE    = 0xFD804000 # reading devmem locks device
CRU_DDRPHY2_BASE    = 0xFD808000 # reading devmem locks device
CRU_DDRPHY3_BASE    = 0xFD80C000 # reading devmem locks device

GRF_BIGCORE0_BASE   = 0xFD590000
GRF_BIGCORE1_BASE   = 0xFD592000
GRF_LITCORE_BASE    = 0xFD594000
GRF_DSU_BASE        = 0xFD598000
GRF_GPU_BASE        = 0xFD5A0000 # reading devmem when gpu_policy != always_on locks device
GRF_NPU_BASE        = 0xFD5A2000 # reading devmem when npu pvtpll not active locks device
GRF_DDR01_BASE      = 0xFD59C000
GRF_DDR23_BASE      = 0xFD59D000

XIN_OSC0_FREQ       = 24        # RK3588 TRM
DEEPSLOW_FREQ       = 0.032768  # RK3588 TRM
CLEAN_FREQ          = 100       # arbirary value

AUPLL_FREQ          = 1572.9    # RK3588 Registers  [m=262, p=2, s=1, k=9437]
CPLL_FREQ           = 1500      # RK3588 Registers  [m=250, p=2, s=1, k=0]
GPLL_FREQ           = 1188      # RK3588 Registers  [m=425, p=2, s=1, k=0]
PPLL_FREQ           = 2200      # RK3588 Registers  [m=550, p=3, s=1, k=0]   
NPLL_FREQ           = 1700      # RK3588 Registers  [m=425, p=3, s=1]
SPLL_FREQ           = 702       # rk3588 dts; can't read from devmem
V0PLL_FREQ          = 1188      # RK3588 Registers  [m=198, p=2, s=1, k=0]

                                # FRACPLL #
                                #   FFVCO = ((m + k / 65536) * FFIN) / p
                                #   FFOUT = ((m + k / 65536) * FFIN) / (p * 2s)

                                # INTPLL #
                                #   FFVCO = (m * FFIN) / p
                                #   FFOUT = (m * FFIN) / (p * 2s)
                                
                                # DDRPLL #
                                #   FFVCO = ((m + k / 65536) * 2 * FFIN) / p
                                #   FFOUT = ((m + k / 65536) * 2 * FFIN) / (p * 2s)

MEMORY_MAP = {
    "CRU_BASE": 0xFD7C0000,
    "CRU_BIGCORE0_BASE": 0xFD810000,
    "CRU_BIGCORE1_BASE": 0xFD812000,
    "CRU_DSU_BASE": 0xFD818000,
    "CRU_DDRPHY0_BASE": 0xFD800000,
    "CRU_DDRPHY1_BASE": 0xFD804000,
    "CRU_DDRPHY2_BASE": 0xFD808000,
    "CRU_DDRPHY3_BASE": 0xFD80C000,
    "GRF_BIGCORE0_BASE": 0xFD590000,
    "GRF_BIGCORE1_BASE": 0xFD592000,
    "GRF_LITCORE_BASE": 0xFD594000,
    "GRF_DSU_BASE": 0xFD598000,
    "GRF_GPU_BASE": 0xFD5A0000,
    "GRF_NPU_BASE": 0xFD5A2000,
}

FLAT_FIELDS_BY_TAB = {}

# Common Functions
def get_bits(value, lsb, msb):
    mask = (1 << (msb - lsb + 1)) - 1
    return (value >> lsb) & mask

def set_bits(orig, value, lsb, msb):
    mask = (1 << (msb - lsb + 1)) - 1
    orig &= ~(mask << lsb)
    orig |= (value & mask) << lsb
    return orig

class Registers:
    def __init__(self, base, size):
        self.mem_fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.mem = mmap.mmap(self.mem_fd, size, mmap.MAP_SHARED,
                             mmap.PROT_READ | mmap.PROT_WRITE, offset=base)

    def read32(self, offset):
        self.mem.seek(offset)
        return struct.unpack("<I", self.mem.read(4))[0]

    def write32(self, offset, value):
        self.mem.seek(offset)
        self.mem.write(struct.pack("<I", value))

    def close(self):
        self.mem.close()
        os.close(self.mem_fd)

reg_mem = {
    key: Registers(addr, REG_SIZE)
    for key, addr in MEMORY_MAP.items()
}

def read_field(field):
    mem, name, offset, lsb, msb, ftype, enum_map, value_range = field
    val = mem.read32(offset)
    width = msb - lsb + 1
    mask = (1 << width) - 1
    raw = (val >> lsb) & mask
    return raw

def write_field(mem, field, user_input, message, flat_fields):
    mem, name, offset, lsb, msb, ftype, enum_map, value_range = field
    user_input = user_input.strip()

    # Safeguard: prevent changing b0pll_pll_reset if bigcore0_mux_sel == "b0pll"
    if name == "b0pll_pll_reset":
        bigcore0_mux_sel_field = next(f for f in flat_fields if f[1] == "bigcore0_mux_sel")
        current_mux_sel = read_field(bigcore0_mux_sel_field)
        if current_mux_sel == "b0pll":
            message[0] = "ERROR: Cannot configure 'b0pll_pll_reset' while 'bigcore0_mux_sel' is set to 'b0pll' — system will lock."
            return False

    # Check b0pll lock before switching bigcore0_mux to b0pll
    if name == "bigcore0_mux_sel":
        if user_input == "b0pll":
            try:
                pll_lock_field = next(f for f in flat_fields if f[1] == "b0pll_lock")
                pll_locked = read_field(pll_lock_field)
                if pll_locked != 1:
                    message[0] = "WARNING: PLL not locked. Set b0pll_pll_reset first."
                    return False
            except StopIteration:
                message[0] = "PLL lock field missing."
                return False

    # Safeguard: prevent changing b1pll_pll_reset if bigcore1_mux_sel == "b1pll"
    if name == "b1pll_pll_reset":
        bigcore1_mux_sel_field = next(f for f in flat_fields if f[1] == "bigcore1_mux_sel")
        current_mux_sel = read_field(bigcore1_mux_sel_field)
        if current_mux_sel == "b1pll":
            message[0] = "ERROR: Cannot configure 'b1pll_pll_reset' while 'bigcore1_mux_sel' is set to 'b1pll' — system will lock."
            return False

    # Check b1pll lock before switching bigcore1_mux to b1pll
    if name == "bigcore1_mux_sel":
        if user_input == "b1pll":
            try:
                pll_lock_field = next(f for f in flat_fields if f[1] == "b1pll_lock")
                pll_locked = read_field(pll_lock_field)
                if pll_locked != 1:
                    message[0] = "WARNING: PLL not locked. Set b1pll_pll_reset first."
                    return False
            except StopIteration:
                message[0] = "PLL lock field missing."
                return False

    # Safeguard: prevent changing lpll_pll_reset if little_mux_sel == "lpll"
    if name == "lpll_pll_reset":
        littlecore_mux_sel_field = next(f for f in flat_fields if f[1] == "littlecore_mux_sel")
        current_mux_sel = read_field(littlecore_mux_sel_field)
        if current_mux_sel == "lpll":
            message[0] = "ERROR: Cannot configure 'lpll_pll_reset' while 'littlecore_mux_sel' is set to 'b1pll' — system will lock."
            return False

    # Check lpll lock before switching littlecore_mux to lpll
    if name == "littlecore_mux_sel":
        if user_input == "lpll":
            try:
                pll_lock_field = next(f for f in flat_fields if f[1] == "lpll_lock")
                pll_locked = read_field(pll_lock_field)
                if pll_locked != 1:
                    message[0] = "WARNING: PLL not locked. Set lpll_pll_reset first."
                    return False
            except StopIteration:
                message[0] = "PLL lock field missing."
                return False

    # Handle enum input:
    if ftype == "enum" and enum_map:
        if user_input in enum_map:
            value = enum_map[user_input]
        else:
            # Maybe user entered a number as string: try to parse and verify
            try:
                int_val = int(user_input)
                if int_val in enum_map.values():
                    value = int_val
                else:
                    message[0] = f"ERROR: Invalid enum integer '{int_val}'. Valid values: {list(enum_map.values())}"
                    return False
            except ValueError:
                message[0] = f"ERROR: Invalid enum value '{user_input}'. Options: {list(enum_map.keys())}"
                return False
    else:
        try:
            value = int(user_input)
        except ValueError:
            message[0] = f"ERROR: Invalid integer input: {user_input}"
            return False

        # Validate range before adjusting
        if value_range and not (value_range[0] <= value <= value_range[1]):
            message[0] = f"ERROR: Value {value} out of range {value_range}"
            return False
        else:
            value = value

    current_val = mem.read32(offset)
    new_val = set_bits(current_val, value, lsb, msb)

    # General write-enable mask: bits 16..(16+(width-1))
    width = msb - lsb + 1
    write_mask = ((1 << width) - 1) << (lsb + 16)
    masked_val = (new_val & 0xFFFF) | write_mask

    mem.write32(offset, masked_val)

    # Verify
    verify = mem.read32(offset)
    verify_val = get_bits(verify, lsb, msb)

    expected_val = value

    if verify_val == expected_val:
        message[0] = f"Successfully wrote {name} = {user_input}"
        return True
    else:
        message[0] = f"ERROR: Failed to verify write for {name}"
        return False

def get_val(name, flat_fields):
    for f in flat_fields:
        if f[1] == name:
            raw = read_field(f)
            ftype = f[5]
            enum_map = f[6] if len(f) > 6 else None
            if ftype == "enum" and enum_map is not None:
                return next((k for k, v in enum_map.items() if v == raw), f"unknown({raw})")
            else:
                return raw
    return None

def is_gpu_safe_to_read():
    try:
        with open("/sys/devices/platform/fb000000.gpu/devfreq/fb000000.gpu/device/power_policy", "r") as f:
            return f.read().strip() == "coarse_demand [always_on]"
    except FileNotFoundError:
        return False  # If sysfs path doesn't exist, assume not safe

def set_gpu_power_policy_always_on():
    try:
        with open("/sys/devices/platform/fb000000.gpu/devfreq/fb000000.gpu/device/power_policy", "w") as f:
            f.write("always_on\n")
        return True
    except PermissionError:
        return False  # Probably need root permissions
    except FileNotFoundError:
        return False

def draw_header(stdscr, current_tab, tabs):
    stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(0, 0, "RK3588 OC Tool by SkatterBencher, v0.8 - Press 'q' to quit | Left/Right to switch tabs")
    stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

    stdscr.move(1, 0)
    stdscr.clrtoeol()  # clear line just to be sure it's empty

    x = 0
    for i, tab_name in enumerate(tabs):
        tab_str = f" {tab_name} "
        if i == current_tab:
            stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
            stdscr.addstr(1, x, tab_str)
            stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)
        else:
            stdscr.attron(curses.color_pair(2))
            stdscr.addstr(1, x, tab_str)
            stdscr.attroff(curses.color_pair(2))
        x += len(tab_str)
    stdscr.hline(2, 0, curses.ACS_HLINE, curses.COLS)

def draw_tab_content(stdscr, current_tab, mem, selected, scroll_offset, message, lpll_freq=0, b0pll_freq=0,b1pll_freq=0):
    if current_tab == 0:
        draw_general_info(stdscr, message="", offset=3)
    elif current_tab == 1:
        draw_bigcore0_ui(stdscr, mem, selected, message, scroll_offset)
    elif current_tab == 2:
        draw_bigcore1_ui(stdscr, mem, selected, message, scroll_offset)
    elif current_tab == 3:
        draw_littlecore_ui(stdscr, mem, selected, message, scroll_offset)
    elif current_tab == 4:
        draw_dsu_ui(stdscr, mem, selected, message, scroll_offset, lpll_freq, b0pll_freq, b1pll_freq)
    elif current_tab == 5:
        draw_gpu_ui(stdscr, mem, selected, message, scroll_offset)
    elif current_tab == 6:
        draw_npu_ui(stdscr, mem, selected, message, scroll_offset)
    #elif current_tab == 7:
    #    draw_dram_ui(stdscr, mem, selected, message, scroll_offset)     // devmem read bus errors cause system freeze
    elif current_tab == 7:
        draw_coming_soon(stdscr, message, offset=3)
        return scroll_offset

def draw_coming_soon(stdscr, message=None, offset=3):
    if message is None:
        message = ["Coming soon..."]
    elif isinstance(message, str):
        message = [message]
    elif not isinstance(message, (list, tuple)):
        message = [str(message)]

    placeholder = "Coming soon..."
    y = (curses.LINES + offset) // 2
    x = max((curses.COLS - len(placeholder)) // 2, 0)
    stdscr.addstr(y, x, placeholder, curses.A_BOLD)

    stdscr.addstr(curses.LINES - 1, 0, message[0])
    stdscr.clrtoeol()

def draw_general_info(stdscr, message="", offset=3):
    paragraphs = [
        "",
        "------------------------------------",
        "## Welcome to the RK3588 OC Tool! ##",
        "------------------------------------",
        "",
        "",        
        "This tool provides access to some (but not all) of the clock configuration registers of the Rockchip RK3588 SoC. It is a work in progress and there may be some bugs.",
        "",
        "",
        "It currently supports BigCore, LittleCore, DSU, and GPU configuration. NPU PVTPLL is not working as is DRAM. You can check the source code for comments.",
        "",
        "",
        "You can refer to the Rockchip RK3588 Technical Reference Manual (TRM) for more details about the register configuration.",        
        "",
        "",
        "Note that this tool is intended for experienced users only. You can brick your device with the wrong settings. Use at your own risk!",
        "",
        "",
        "Enjoy tuning your RK3588! - SkatterBencher",
        ""
    ]

    max_width = int(curses.COLS * 0.80)
    start_row = offset
    row = start_row

    for paragraph in paragraphs:
        if paragraph == "":
            row += 1
            continue

        wrapped_lines = textwrap.wrap(paragraph, width=max_width)
        
        for line in wrapped_lines:
            x = (curses.COLS - max_width) // 2 + max((max_width - len(line)) // 2, 0)
            stdscr.addstr(row, x, line)
            row += 1

    stdscr.move(curses.LINES - 1, 0)
    stdscr.clrtoeol()

    if message:
        stdscr.addstr(curses.LINES - 1, 0, str(message))

def draw_bigcore0_ui(stdscr, mem, selected, message, scroll_offset):
    FIELD_NAME_COL_WIDTH = 25
    VALUE_COL_WIDTH = 15
    INFO_COL_WIDTH = 35

    start_row = 2
    visible_rows = curses.LINES - start_row - 2

    # CRU & GRF Offsets
    BIGCORE0_B0PLL_CON0 = 0x0000
    BIGCORE0_B0PLL_CON1 = 0x0004
    BIGCORE0_B0PLL_CON6 = 0x0018
    BIGCORE0_MODE_CON00 = 0x0280
    BIGCORE0_CLKSEL_CON00 = 0x0300
    BIGCORE0_CLKSEL_CON01 = 0x0304
    GRF_BIGCORE0_PVTPLL_CON0_L = 0x0000
    GRF_BIGCORE0_PVTPLL_CON0_H = 0x0004
    GRF_BIGCORE0_PVTPLL = 0x18

    def read_pvtpll_freq(mem_grf):
        # Read 32-bit value at offset (0x18)
        freq_mhz = mem_grf.read32(GRF_BIGCORE0_PVTPLL)
        return freq_mhz

    # Clocking Parameters
    SECTIONS = [
        ("## bigcore0 pvtpll configuration ##", "GRF_BIGCORE0_BASE", [
            ("osc_ring_sel", GRF_BIGCORE0_PVTPLL_CON0_L, (8, 10), "int", None, (0, 7)),
                # 0 = HDBLVT20_INV_S_4, 1 = HDBLVT22_INV_S_4, 2 = Reserved, 3 = HDBSVT22_INV_S_4
                # 4 = HDBLVT20_INV_SHSDB_4, 5 = HDBLVT22_INV_SHSDB_4, 6 = Reserved, 7 = HDBSVT22_INV_SHSDB_4
            ("ring_length_sel", GRF_BIGCORE0_PVTPLL_CON0_H, (0, 5), "int", None, (0, 63)), #number of inventers = (n+5)*2
        ]),
        ("## b0pll configuration ##", "CRU_BIGCORE0_BASE", [
            ("m_b0pll", BIGCORE0_B0PLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_b0pll", BIGCORE0_B0PLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_b0pll", BIGCORE0_B0PLL_CON1, (6, 8), "int", None, (0, 6)),
            ("clk_b0pll_mux", BIGCORE0_MODE_CON00, (0, 1), "enum",
             {"xin_osc0_func": 0b00, "clk_b0pll": 0b01, "clk_deepslow": 0b10}),
            ("b0pll_pll_reset", BIGCORE0_B0PLL_CON1, (13, 13), "int", None, (0, 1)),
            ("b0pll_lock", BIGCORE0_B0PLL_CON6, (15, 15), "int", None, (0, 1)),
        ]),
        ("## bigcore0 mux configuration ##", "CRU_BIGCORE0_BASE", [
            ("bigcore0_slow_sel", BIGCORE0_CLKSEL_CON00, (0, 0), "enum",
             {"xin_osc0_func": 0b0, "clk_deepslow": 0b1}),
            ("bigcore0_gpll_div", BIGCORE0_CLKSEL_CON00, (1, 5), "int", None, (0, 31)),
            ("bigcore0_mux_sel", BIGCORE0_CLKSEL_CON00, (6, 7), "enum",
             {"slow": 0b00, "gpll": 0b01, "b0pll": 0b10}),
            # ("bigcore0_pvtpll_sel", BIGCORE0_CLKSEL_CON01, (14, 14), "enum",  // requires updating of cal_cnt register (0x8)
            #  {"bigcore0_mux": 0b0, "xin_osc0_func": 0b1,}),                   // no logic implemented
        ]),
        ("## core configuration ##", "CRU_BIGCORE0_BASE", [
            ("b0_uc_div", BIGCORE0_CLKSEL_CON00, (8, 12), "int", None, (0, 31)),
            ("b0_clk_sel", BIGCORE0_CLKSEL_CON00, (13, 14), "enum",
             {"UC_b0": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
            ("b1_uc_div", BIGCORE0_CLKSEL_CON01, (0, 4), "int", None, (0, 31)),
            ("b1_clk_sel", BIGCORE0_CLKSEL_CON01, (5, 6), "enum",
             {"UC_b1": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
        ]),
    ]

    FLAT_FIELDS = []
    for _, base_tag, fields in SECTIONS:
        mem_obj = reg_mem[base_tag]
        for entry in fields:
            name, offset, bit_range, ftype = entry[:4]
            enum_map = entry[4] if len(entry) > 4 else None
            val_range = entry[5] if len(entry) > 5 else None
            lsb, msb = bit_range
            FLAT_FIELDS.append((mem_obj, name, offset, lsb, msb, ftype, enum_map, val_range))

    # Gather fields to display
    display_lines = []
    idx = 0
    for section_title, base_tag, fields in SECTIONS:
        display_lines.append(("", "spacer"))
        display_lines.append((section_title, "section"))
        for field in fields:
            name, offset, bit_range, ftype = field[:4]
            enum_map = field[4] if len(field) > 4 else None
            val_range = field[5] if len(field) > 5 else None
            lsb, msb = bit_range

            mem_obj = reg_mem[base_tag]
            reg_val = mem_obj.read32(offset)
            val = get_bits(reg_val, lsb, msb)

            if ftype == "enum":
                disp_val = next((k for k, v in enum_map.items() if v == val), f"unknown({val})")
                extra = f"Options: {list(enum_map.keys())}"
            else:
                disp_val = str(val)
                extra = f"Range: {val_range}" if val_range else ""

            display_lines.append(((name, disp_val, extra), "field", idx))
            idx += 1

    m_b0 = get_val("m_b0pll", FLAT_FIELDS)
    p_b0 = get_val("p_b0pll", FLAT_FIELDS)
    s_b0 = get_val("s_b0pll", FLAT_FIELDS)

    b0pll_freq = (XIN_OSC0_FREQ * m_b0) / p_b0 / (1 << s_b0) if m_b0 and p_b0 else 0

    bigcore0_slow_sel = get_val("bigcore0_slow_sel", FLAT_FIELDS)
    bigcore0_gpll_div = get_val("bigcore0_gpll_div", FLAT_FIELDS)
    bigcore0_mux_sel = get_val("bigcore0_mux_sel", FLAT_FIELDS)

    b0_uc_div = get_val("b0_uc_div", FLAT_FIELDS)
    b1_uc_div = get_val("b1_uc_div", FLAT_FIELDS)

    b0_clk_sel = get_val("b0_clk_sel", FLAT_FIELDS)
    b1_clk_sel = get_val("b1_clk_sel", FLAT_FIELDS)

    if bigcore0_mux_sel == "slow":
        bigcore0_mux_clk = XIN_OSC0_FREQ if bigcore0_slow_sel == "xin_osc0_func" else DEEPSLOW_FREQ
    elif bigcore0_mux_sel == "gpll":
        bigcore0_mux_clk = GPLL_FREQ / (bigcore0_gpll_div + 1)
    elif bigcore0_mux_sel == "b0pll":
        bigcore0_mux_clk = b0pll_freq
    else:
        bigcore0_mux_clk = 0

    b0_uc_clk = bigcore0_mux_clk / (b0_uc_div + 1)
    b1_uc_clk = bigcore0_mux_clk / (b1_uc_div + 1)

    mem_grf = Registers(GRF_BIGCORE0_BASE, 0x1000)
    bigcore0_pvtpll_freq = read_pvtpll_freq(mem_grf)
    mem_grf.close()

    def get_clk_freq(sel, uc_clk):
        if sel in ("UC_b0", "UC_b1"):
            return uc_clk
        elif sel == "Clean":
            return CLEAN_FREQ
        elif sel == "PVTPLL":
            return bigcore0_pvtpll_freq
        else:
            return 0

    b0_clk_freq = get_clk_freq(b0_clk_sel, b0_uc_clk)
    b1_clk_freq = get_clk_freq(b1_clk_sel, b1_uc_clk)

    freq_lines = [
        f"GPLL Frequency:       {GPLL_FREQ:.0f} MHz",
        f"B0PLL Frequency:      {b0pll_freq:.0f} MHz",
        f"Bigcore0 MUX Freq:    {bigcore0_mux_clk:.0f} MHz from {bigcore0_mux_sel}",
        f"Bigcore0 PVTPLL Freq: {bigcore0_pvtpll_freq:.0f} MHz",
        f"B0 UC Frequency:      {b0_uc_clk:.0f} MHz",
        f"B1 UC Frequency:      {b1_uc_clk:.0f} MHz",
        f"--------------",
        f"B0 Clock Frequency:   {b0_clk_freq:.0f} MHz from {b0_clk_sel}",
        f"B1 Clock Frequency:   {b1_clk_freq:.0f} MHz from {b1_clk_sel}",
    ]

    # Add spacer before frequency section
    display_lines.append(("", "spacer"))
    display_lines.append(("## frequency configuration ##", "section"))

    # Add frequency lines as freq entries (no index needed)
    for line in freq_lines:
        display_lines.append(((None, line, ""), "freq"))

    # Clamp scroll_offset
    if scroll_offset is None:
        scroll_offset = 0
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + visible_rows:
        scroll_offset = selected - visible_rows + 1

    # Render visible lines
    for visible_idx, (entry, etype, *rest) in enumerate(display_lines[scroll_offset:scroll_offset + visible_rows]):
        row = start_row + visible_idx 

        if etype == "spacer":
            continue
        elif etype == "section":
            stdscr.addstr(row, 0, entry, curses.color_pair(1) | curses.A_BOLD)
        elif etype == "field":
            name, disp_val, extra = entry
            field_idx = rest[0]
            highlight = curses.A_REVERSE if field_idx == selected else curses.A_NORMAL
            line = f"{name:<{FIELD_NAME_COL_WIDTH}}: {disp_val:<{VALUE_COL_WIDTH}} {extra:<{INFO_COL_WIDTH}}"
            stdscr.addstr(row, 2, line[:curses.COLS - 3], highlight)
        elif etype == "freq":
            _, line, _ = entry
            stdscr.addstr(row, 2, line[:curses.COLS - 3])

    return scroll_offset, FLAT_FIELDS, b0pll_freq

def draw_bigcore1_ui(stdscr, mem, selected, message, scroll_offset):
    FIELD_NAME_COL_WIDTH = 25
    VALUE_COL_WIDTH = 15
    INFO_COL_WIDTH = 35

    start_row = 2
    visible_rows = curses.LINES - start_row - 2

    # CRU & GRF Offsets
    BIGCORE1_B1PLL_CON0 = 0x0020
    BIGCORE1_B1PLL_CON1 = 0x0024
    BIGCORE1_B1PLL_CON6 = 0x0038
    BIGCORE1_MODE_CON00 = 0x0280
    BIGCORE1_CLKSEL_CON00 = 0x0300
    BIGCORE1_CLKSEL_CON01 = 0x0304
    GRF_BIGCORE1_PVTPLL_CON0_L = 0x0000
    GRF_BIGCORE1_PVTPLL_CON0_H = 0x0004
    GRF_BIGCORE1_PVTPLL = 0x18

    def read_pvtpll_freq(mem_grf):
        # Read 32-bit value at offset (0x18)
        freq_mhz = mem_grf.read32(GRF_BIGCORE1_PVTPLL)
        return freq_mhz

    # Clocking Parameters
    SECTIONS = [
        ("## bigcore1 pvtpll configuration ##", "GRF_BIGCORE1_BASE", [
            ("osc_ring_sel", GRF_BIGCORE1_PVTPLL_CON0_L, (8, 10), "int", None, (0, 7)),
                # 0 = HDBLVT20_INV_S_4, 1 = HDBLVT22_INV_S_4, 2 = Reserved, 3 = HDBSVT22_INV_S_4
                # 4 = HDBLVT20_INV_SHSDB_4, 5 = HDBLVT22_INV_SHSDB_4, 6 = Reserved, 7 = HDBSVT22_INV_SHSDB_4
            ("ring_length_sel", GRF_BIGCORE1_PVTPLL_CON0_H, (0, 5), "int", None, (0, 63)), #number of inventers = (n+5)*2
        ]),
        ("## b1pll configuration ##", "CRU_BIGCORE1_BASE", [
            ("m_b1pll", BIGCORE1_B1PLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_b1pll", BIGCORE1_B1PLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_b1pll", BIGCORE1_B1PLL_CON1, (6, 8), "int", None, (0, 6)),
            ("clk_b1pll_mux", BIGCORE1_MODE_CON00, (0, 1), "enum",
             {"xin_osc0_func": 0b00, "clk_b1pll": 0b01, "clk_deepslow": 0b10}),
            ("b1pll_pll_reset", BIGCORE1_B1PLL_CON1, (13, 13), "int", None, (0, 1)),
            ("b1pll_lock", BIGCORE1_B1PLL_CON6, (15, 15), "int", None, (0, 1)),
        ]),
        ("## bigcore1 mux configuration ##", "CRU_BIGCORE1_BASE", [
            ("bigcore1_slow_sel", BIGCORE1_CLKSEL_CON00, (0, 0), "enum",
             {"xin_osc0_func": 0b0, "clk_deepslow": 0b1}),
            ("bigcore1_gpll_div", BIGCORE1_CLKSEL_CON00, (1, 5), "int", None, (0, 31)),
            ("bigcore1_mux_sel", BIGCORE1_CLKSEL_CON00, (6, 7), "enum",
             {"slow": 0b00, "gpll": 0b01, "b1pll": 0b10}),
            # ("bigcore1_pvtpll_sel", BIGCORE1_CLKSEL_CON01, (14, 14), "enum",  // requires updating of cal_cnt register (0x8)
            #  {"bigcore1_mux": 0b0, "xin_osc0_func": 0b1,}),                   // no logic implemented
        ]),
        ("## core configuration ##", "CRU_BIGCORE1_BASE", [
            ("b2_uc_div", BIGCORE1_CLKSEL_CON00, (8, 12), "int", None, (0, 31)),
            ("b2_clk_sel", BIGCORE1_CLKSEL_CON00, (13, 14), "enum",
             {"UC_b2": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
            ("b3_uc_div", BIGCORE1_CLKSEL_CON01, (0, 4), "int", None, (0, 31)),
            ("b3_clk_sel", BIGCORE1_CLKSEL_CON01, (5, 6), "enum",
             {"UC_b3": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
        ]),
    ]

    FLAT_FIELDS = []
    for _, base_tag, fields in SECTIONS:
        mem_obj = reg_mem[base_tag]
        for entry in fields:
            name, offset, bit_range, ftype = entry[:4]
            enum_map = entry[4] if len(entry) > 4 else None
            val_range = entry[5] if len(entry) > 5 else None
            lsb, msb = bit_range
            FLAT_FIELDS.append((mem_obj, name, offset, lsb, msb, ftype, enum_map, val_range))

    # Gather fields to display
    display_lines = []
    idx = 0
    for section_title, base_tag, fields in SECTIONS:
        display_lines.append(("", "spacer"))
        display_lines.append((section_title, "section"))
        for field in fields:
            name, offset, bit_range, ftype = field[:4]
            enum_map = field[4] if len(field) > 4 else None
            val_range = field[5] if len(field) > 5 else None
            lsb, msb = bit_range

            mem_obj = reg_mem[base_tag]
            reg_val = mem_obj.read32(offset)
            val = get_bits(reg_val, lsb, msb)

            if ftype == "enum":
                disp_val = next((k for k, v in enum_map.items() if v == val), f"unknown({val})")
                extra = f"Options: {list(enum_map.keys())}"
            else:
                disp_val = str(val)
                extra = f"Range: {val_range}" if val_range else ""

            display_lines.append(((name, disp_val, extra), "field", idx))
            idx += 1

    m_b1 = get_val("m_b1pll", FLAT_FIELDS)
    p_b1 = get_val("p_b1pll", FLAT_FIELDS)
    s_b1 = get_val("s_b1pll", FLAT_FIELDS)

    b1pll_freq = (XIN_OSC0_FREQ * m_b1) / p_b1 / (1 << s_b1) if m_b1 and p_b1 else 0

    bigcore1_slow_sel = get_val("bigcore1_slow_sel", FLAT_FIELDS)
    bigcore1_gpll_div = get_val("bigcore1_gpll_div", FLAT_FIELDS)
    bigcore1_mux_sel = get_val("bigcore1_mux_sel", FLAT_FIELDS)

    b2_uc_div = get_val("b2_uc_div", FLAT_FIELDS)
    b3_uc_div = get_val("b3_uc_div", FLAT_FIELDS)

    b2_clk_sel = get_val("b2_clk_sel", FLAT_FIELDS)
    b3_clk_sel = get_val("b3_clk_sel", FLAT_FIELDS)

    if bigcore1_mux_sel == "slow":
        bigcore1_mux_clk = XIN_OSC0_FREQ if bigcore1_slow_sel == "xin_osc0_func" else DEEPSLOW_FREQ
    elif bigcore1_mux_sel == "gpll":
        bigcore1_mux_clk = GPLL_FREQ / (bigcore1_gpll_div + 1)
    elif bigcore1_mux_sel == "b1pll":
        bigcore1_mux_clk = b1pll_freq
    else:
        bigcore1_mux_clk = 0

    b2_uc_clk = bigcore1_mux_clk / (b2_uc_div + 1)
    b3_uc_clk = bigcore1_mux_clk / (b3_uc_div + 1)

    mem_grf = Registers(GRF_BIGCORE1_BASE, 0x1000)
    bigcore1_pvtpll_freq = read_pvtpll_freq(mem_grf)
    mem_grf.close()

    def get_clk_freq(sel, uc_clk):
        if sel in ("UC_b2", "UC_b3"):
            return uc_clk
        elif sel == "Clean":
            return CLEAN_FREQ
        elif sel == "PVTPLL":
            return bigcore1_pvtpll_freq
        else:
            return 0

    b2_clk_freq = get_clk_freq(b2_clk_sel, b2_uc_clk)
    b3_clk_freq = get_clk_freq(b3_clk_sel, b3_uc_clk)

    freq_lines = [
        f"GPLL Frequency:       {GPLL_FREQ:.0f} MHz",
        f"B1PLL Frequency:      {b1pll_freq:.0f} MHz",
        f"Bigcore1 MUX Freq:    {bigcore1_mux_clk:.0f} MHz from {bigcore1_mux_sel}",
        f"Bigcore1 PVTPLL Freq: {bigcore1_pvtpll_freq:.0f} MHz",
        f"B2 UC Frequency:      {b2_uc_clk:.0f} MHz",
        f"B3 UC Frequency:      {b3_uc_clk:.0f} MHz",
        f"--------------",
        f"B2 Clock Frequency:   {b2_clk_freq:.0f} MHz from {b2_clk_sel}",
        f"B3 Clock Frequency:   {b3_clk_freq:.0f} MHz from {b3_clk_sel}",
    ]

    # Add spacer before frequency section
    display_lines.append(("", "spacer"))
    display_lines.append(("## frequency configuration ##", "section"))

    # Add frequency lines as freq entries (no index needed)
    for line in freq_lines:
        display_lines.append(((None, line, ""), "freq"))

    # Clamp scroll_offset
    if scroll_offset is None:
        scroll_offset = 0
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + visible_rows:
        scroll_offset = selected - visible_rows + 1

    # Render visible lines
    for visible_idx, (entry, etype, *rest) in enumerate(display_lines[scroll_offset:scroll_offset + visible_rows]):
        row = start_row + visible_idx 

        if etype == "spacer":
            continue
        elif etype == "section":
            stdscr.addstr(row, 0, entry, curses.color_pair(1) | curses.A_BOLD)
        elif etype == "field":
            name, disp_val, extra = entry
            field_idx = rest[0]
            highlight = curses.A_REVERSE if field_idx == selected else curses.A_NORMAL
            line = f"{name:<{FIELD_NAME_COL_WIDTH}}: {disp_val:<{VALUE_COL_WIDTH}} {extra:<{INFO_COL_WIDTH}}"
            stdscr.addstr(row, 2, line[:curses.COLS - 3], highlight)
        elif etype == "freq":
            _, line, _ = entry
            stdscr.addstr(row, 2, line[:curses.COLS - 3])

    return scroll_offset, FLAT_FIELDS, b1pll_freq

def draw_littlecore_ui(stdscr, mem, selected, message, scroll_offset):
    FIELD_NAME_COL_WIDTH = 25
    VALUE_COL_WIDTH = 15
    INFO_COL_WIDTH = 35

    start_row = 2
    visible_rows = curses.LINES - start_row - 2

    # CRU & GRF Offsets
    DSU_LPLL_CON0 = 0x0040
    DSU_LPLL_CON1 = 0x0044
    DSU_LPLL_CON6 = 0x0058
    DSU_MODE_CON00 = 0x0280
    DSU_CLKSEL_CON00 = 0x0300
    DSU_CLKSEL_CON01 = 0x0304
    DSU_CLKSEL_CON02 = 0x0308
    DSU_CLKSEL_CON03 = 0x030C
    DSU_CLKSEL_CON04 = 0x0310
    DSU_CLKSEL_CON05 = 0x0314
    DSU_CLKSEL_CON06 = 0x0318
    DSU_CLKSEL_CON07 = 0x031C
    GRF_LITCORE_PVTPLL_CON0_L = 0x40
    GRF_LITCORE_PVTPLL_CON0_H = 0x44
    GRF_LITCORE_PVTPLL = 0x60

    def read_pvtpll_freq(mem_grf):
        freq_mhz = mem_grf.read32(GRF_LITCORE_PVTPLL)
        return freq_mhz

    # Clocking Parameters
    SECTIONS = [
        ("## littlecore pvtpll configuration ##", "GRF_LITCORE_BASE", [
            ("ring_length_sel", GRF_LITCORE_PVTPLL_CON0_L, (8, 10), "int", None, (0, 7)),
                # 0 = HDBLVT20_INV_S_4, 1 = HDBLVT22_INV_S_4, 2 = Reserved, 3 = HDBSVT22_INV_S_4
                # 4 = HDBLVT20_INV_SHSDB_4, 5 = HDBLVT22_INV_SHSDB_4, 6 = Reserved, 7 = HDBSVT22_INV_SHSDB_4
            ("ring_length_sel", GRF_LITCORE_PVTPLL_CON0_H, (0, 5), "int", None, (0, 63)), #number of inventers = (n+5)*2
        ]),
        ("## lpll configuration ##", "CRU_DSU_BASE", [
            ("m_lpll", DSU_LPLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_lpll", DSU_LPLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_lpll", DSU_LPLL_CON1, (6, 8), "int", None, (0, 6)),
            ("clk_lpll_mux", DSU_MODE_CON00, (0, 1), "enum",
             {"xin_osc0_func": 0b00, "clk_lpll": 0b01, "clk_deepslow": 0b10}),
            ("lpll_pll_reset", DSU_LPLL_CON1, (13, 13), "int", None, (0, 1)),
            ("lpll_lock", DSU_LPLL_CON6, (15, 15), "int", None, (0, 1)),
        ]),
        ("## littlecore mux configuration ##", "CRU_DSU_BASE", [
            ("littlecore_slow_sel", DSU_CLKSEL_CON00, (0, 0), "enum",
             {"xin_osc0_func": 0b0, "clk_deepslow": 0b1}),
            ("littlecore_gpll_div", DSU_CLKSEL_CON05, (9, 13), "int", None, (0, 31)),
            ("littlecore_mux_sel", DSU_CLKSEL_CON05, (14, 15), "enum",
             {"slow": 0b00, "gpll": 0b01, "lpll": 0b10}),
            # ("littlecore_pvtpll_sel", DSU_CLKSEL_CON04, (9, 9), "enum",   // requires updating of cal_cnt register (0x48)
            #  {"littlecore_mux": 0b0, "xin_osc0_func": 0b1,}),             // no logic implemented
        ]),
        ("## core configuration ##", "CRU_DSU_BASE", [
            ("l0_uc_div", DSU_CLKSEL_CON06, (0, 4), "int", None, (0, 31)),
            ("l0_clk_sel", DSU_CLKSEL_CON06, (5, 6), "enum",
             {"UC_l0": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
            ("l1_uc_div", DSU_CLKSEL_CON06, (7, 11), "int", None, (0, 31)),
            ("l1_clk_sel", DSU_CLKSEL_CON06, (12, 13), "enum",
             {"UC_l1": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
            ("l2_uc_div", DSU_CLKSEL_CON07, (0, 4), "int", None, (0, 31)),
            ("l2_clk_sel", DSU_CLKSEL_CON07, (5, 6), "enum",
             {"UC_l2": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
            ("l3_uc_div", DSU_CLKSEL_CON07, (7, 11), "int", None, (0, 31)),
            ("l3_clk_sel", DSU_CLKSEL_CON07, (12, 13), "enum",
             {"UC_l3": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
        ]),
    ]

    FLAT_FIELDS = []
    for _, base_tag, fields in SECTIONS:
        mem_obj = reg_mem[base_tag]
        for entry in fields:
            name, offset, bit_range, ftype = entry[:4]
            enum_map = entry[4] if len(entry) > 4 else None
            val_range = entry[5] if len(entry) > 5 else None
            lsb, msb = bit_range
            FLAT_FIELDS.append((mem_obj, name, offset, lsb, msb, ftype, enum_map, val_range))

    # Gather fields to display
    display_lines = []
    idx = 0
    for section_title, base_tag, fields in SECTIONS:
        display_lines.append(("", "spacer"))
        display_lines.append((section_title, "section"))
        for field in fields:
            name, offset, bit_range, ftype = field[:4]
            enum_map = field[4] if len(field) > 4 else None
            val_range = field[5] if len(field) > 5 else None
            lsb, msb = bit_range

            mem_obj = reg_mem[base_tag]
            reg_val = mem_obj.read32(offset)
            val = get_bits(reg_val, lsb, msb)

            if ftype == "enum":
                disp_val = next((k for k, v in enum_map.items() if v == val), f"unknown({val})")
                extra = f"Options: {list(enum_map.keys())}"
            else:
                disp_val = str(val)
                extra = f"Range: {val_range}" if val_range else ""

            display_lines.append(((name, disp_val, extra), "field", idx))
            idx += 1

    # Gather GRF data
    mem_grf = Registers(GRF_LITCORE_BASE, 0x1000)
    littlecore_pvtpll_freq = read_pvtpll_freq(mem_grf)
    mem_grf.close()

    # Gather CRU data
    m_l = get_val("m_lpll", FLAT_FIELDS)
    p_l = get_val("p_lpll", FLAT_FIELDS)
    s_l = get_val("s_lpll", FLAT_FIELDS)

    lpll_freq = (XIN_OSC0_FREQ * m_l) / p_l / (1 << s_l) if m_l and p_l else 0

    littlecore_slow_sel = get_val("littlecore_slow_sel", FLAT_FIELDS)
    littlecore_gpll_div = get_val("littlecore_gpll_div", FLAT_FIELDS)
    littlecore_mux_sel = get_val("littlecore_mux_sel", FLAT_FIELDS)

    l0_uc_div = get_val("l0_uc_div", FLAT_FIELDS)
    l1_uc_div = get_val("l1_uc_div", FLAT_FIELDS)
    l2_uc_div = get_val("l2_uc_div", FLAT_FIELDS)
    l3_uc_div = get_val("l3_uc_div", FLAT_FIELDS)

    l0_clk_sel = get_val("l0_clk_sel", FLAT_FIELDS)
    l1_clk_sel = get_val("l1_clk_sel", FLAT_FIELDS)
    l2_clk_sel = get_val("l2_clk_sel", FLAT_FIELDS)
    l3_clk_sel = get_val("l3_clk_sel", FLAT_FIELDS)

    if littlecore_mux_sel == "slow":
        littlecore_mux_clk = XIN_OSC0_FREQ if littlecore_slow_sel == "xin_osc0_func" else DEEPSLOW_FREQ
    elif littlecore_mux_sel == "gpll":
        littlecore_mux_clk = GPLL_FREQ / (littlecore_gpll_div + 1)
    elif littlecore_mux_sel == "lpll":
        littlecore_mux_clk = lpll_freq
    else:
        littlecore_mux_clk = 0

    l0_uc_clk = littlecore_mux_clk / (l0_uc_div + 1)
    l1_uc_clk = littlecore_mux_clk / (l1_uc_div + 1)
    l2_uc_clk = littlecore_mux_clk / (l2_uc_div + 1)
    l3_uc_clk = littlecore_mux_clk / (l3_uc_div + 1)

    def get_clk_freq(sel, uc_clk):
        if sel in ("UC_l0", "UC_l1", "UC_l2", "UC_l3"):
            return uc_clk
        elif sel == "Clean":
            return CLEAN_FREQ
        elif sel == "PVTPLL":
            return littlecore_pvtpll_freq
        else:
            return 0

    l0_clk_freq = get_clk_freq(l0_clk_sel, l0_uc_clk)
    l1_clk_freq = get_clk_freq(l1_clk_sel, l1_uc_clk)
    l2_clk_freq = get_clk_freq(l2_clk_sel, l2_uc_clk)
    l3_clk_freq = get_clk_freq(l3_clk_sel, l3_uc_clk)

    freq_lines = [
        f"GPLL Frequency:           {GPLL_FREQ:.0f} MHz",
        f"LPLL Frequency:           {lpll_freq:.0f} MHz",
        f"Littlecore MUX Freq:      {littlecore_mux_clk:.0f} MHz from {littlecore_mux_sel}",
        f"Littlecore PVTPLL Freq:   {littlecore_pvtpll_freq:.0f} MHz",
        f"L0 UC Frequency:          {l0_uc_clk:.0f} MHz",
        f"L1 UC Frequency:          {l1_uc_clk:.0f} MHz",
        f"L2 UC Frequency:          {l2_uc_clk:.0f} MHz",
        f"L3 UC Frequency:          {l3_uc_clk:.0f} MHz",
        f"--------------",
        f"L0 Clock Frequency:       {l0_clk_freq:.0f} MHz from {l0_clk_sel}",
        f"L1 Clock Frequency:       {l1_clk_freq:.0f} MHz from {l1_clk_sel}",
        f"L2 Clock Frequency:       {l2_clk_freq:.0f} MHz from {l2_clk_sel}",
        f"L3 Clock Frequency:       {l3_clk_freq:.0f} MHz from {l3_clk_sel}",
    ]

    # Add spacer before frequency section
    display_lines.append(("", "spacer"))
    display_lines.append(("## frequency configuration ##", "section"))

    # Add frequency lines as freq entries (no index needed)
    for line in freq_lines:
        display_lines.append(((None, line, ""), "freq"))

    # Clamp scroll_offset
    if scroll_offset is None:
        scroll_offset = 0
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + visible_rows:
        scroll_offset = selected - visible_rows + 1

    # Render visible lines
    for visible_idx, (entry, etype, *rest) in enumerate(display_lines[scroll_offset:scroll_offset + visible_rows]):
        row = start_row + visible_idx 

        if etype == "spacer":
            continue
        elif etype == "section":
            stdscr.addstr(row, 0, entry, curses.color_pair(1) | curses.A_BOLD)
        elif etype == "field":
            name, disp_val, extra = entry
            field_idx = rest[0]
            highlight = curses.A_REVERSE if field_idx == selected else curses.A_NORMAL
            line = f"{name:<{FIELD_NAME_COL_WIDTH}}: {disp_val:<{VALUE_COL_WIDTH}} {extra:<{INFO_COL_WIDTH}}"
            stdscr.addstr(row, 2, line[:curses.COLS - 3], highlight)
        elif etype == "freq":
            _, line, _ = entry
            stdscr.addstr(row, 2, line[:curses.COLS - 3])

    return scroll_offset, FLAT_FIELDS, lpll_freq

def draw_dsu_ui(stdscr, mem, selected, message, scroll_offset, lpll_freq=0, b0pll_freq=0, b1pll_freq=0):
    FIELD_NAME_COL_WIDTH = 25
    VALUE_COL_WIDTH = 15
    INFO_COL_WIDTH = 35

    start_row = 2
    visible_rows = curses.LINES - start_row - 2

    # CRU & GRF Offsets
    DSU_LPLL_CON0 = 0x0040
    DSU_LPLL_CON1 = 0x0044
    DSU_LPLL_CON6 = 0x0058
    DSU_MODE_CON00 = 0x0280
    DSU_CLKSEL_CON00 = 0x0300
    DSU_CLKSEL_CON01 = 0x0304
    DSU_CLKSEL_CON02 = 0x0308
    DSU_CLKSEL_CON03 = 0x030C
    DSU_CLKSEL_CON04 = 0x0310
    DSU_CLKSEL_CON05 = 0x0314
    DSU_CLKSEL_CON06 = 0x0318
    DSU_CLKSEL_CON07 = 0x031C
    GRF_DSU_PVTPLL_CON0_L = 0x60
    GRF_DSU_PVTPLL_CON0_H = 0x64
    GRF_DSU_PVTPLL = 0x80

    def read_pvtpll_freq(mem_grf):
        freq_mhz = mem_grf.read32(GRF_DSU_PVTPLL)
        return freq_mhz

    # Clocking Parameters
    SECTIONS = [
        ("## dsu pvtpll configuration ##", "GRF_DSU_BASE", [
            ("ring_length_sel", GRF_DSU_PVTPLL_CON0_L, (8, 10), "int", None, (0, 7)),
                # 0 = HDBLVT20_INV_S_4, 1 = HDBLVT22_INV_S_4, 2 = Reserved, 3 = HDBSVT22_INV_S_4
                # 4 = HDBLVT20_INV_SHSDB_4, 5 = HDBLVT22_INV_SHSDB_4, 6 = Reserved, 7 = HDBSVT22_INV_SHSDB_4
            ("ring_length_sel", GRF_DSU_PVTPLL_CON0_H, (0, 5), "int", None, (0, 63)), #number of inventers = (n+5)*2
        ]),
        ("## sclk_dsu configuration ##", "CRU_DSU_BASE", [
            ("dsu_sclk_df_src_mux_sel", DSU_CLKSEL_CON00, (12, 13), "enum",
             {"b0pll": 0b00, "b1pll": 0b01, "lpll": 0b10, "gpll": 0b11}),
            ("dsu_sclk_df_src_mux_div", DSU_CLKSEL_CON00, (7, 11), "int", None, (0, 31)),
            ("dsu_sclk_src_t_sel", DSU_CLKSEL_CON01, (0, 0), "enum",
             {"dsu_src": 0b0, "PVTPLL": 0b01}),
            #("dsu_pvtpll_sel", DSU_CLKSEL_CON04, (10, 10), "enum", // requires updating of cal_cnt register (0x70)
            # {"dsu_sclk_df_src": 0b0, "xin_osc0_func": 0b1,}),     // no logic implemented
        ]),
        ("## pclk_dsu configuration ##", "CRU_DSU_BASE", [
            ("dsu_pclk_root_mux_sel", DSU_CLKSEL_CON04, (5, 6), "enum",
             {"b0pll": 0b00, "b1pll": 0b01, "lpll": 0b10, "gpll": 0b11}),
            ("dsu_pclk_root_mux_div", DSU_CLKSEL_CON04, (0, 4), "int", None, (0, 31)),
        ]),
        ("## dsu_other configuration ##", "CRU_DSU_BASE", [
            ("dsu_aclkm_div", DSU_CLKSEL_CON01, (1, 5), "int", None, (0, 31)),
            ("dsu_aclks_div", DSU_CLKSEL_CON01, (6, 10), "int", None, (0, 31)),
            ("dsu_aclkmp_div", DSU_CLKSEL_CON01, (11, 15), "int", None, (0, 31)),
            ("dsu_periphclk_div", DSU_CLKSEL_CON02, (0, 4), "int", None, (0, 31)),
            ("dsu_cntclk_div", DSU_CLKSEL_CON02, (5, 9), "int", None, (0, 31)),
            ("dsu_tsclk_div", DSU_CLKSEL_CON02, (10, 14), "int", None, (0, 31)),
            ("dsu_atclk_div", DSU_CLKSEL_CON03, (0, 4), "int", None, (0, 31)),
            ("dsu_gicclk_t_div", DSU_CLKSEL_CON03, (5, 9), "int", None, (0, 31)),
        ]),
    ]

    FLAT_FIELDS = []
    for _, base_tag, fields in SECTIONS:
        mem_obj = reg_mem[base_tag]
        for entry in fields:
            name, offset, bit_range, ftype = entry[:4]
            enum_map = entry[4] if len(entry) > 4 else None
            val_range = entry[5] if len(entry) > 5 else None
            lsb, msb = bit_range
            FLAT_FIELDS.append((mem_obj, name, offset, lsb, msb, ftype, enum_map, val_range))

    # Gather fields to display
    display_lines = []
    idx = 0
    for section_title, base_tag, fields in SECTIONS:
        display_lines.append(("", "spacer"))
        display_lines.append((section_title, "section"))
        for field in fields:
            name, offset, bit_range, ftype = field[:4]
            enum_map = field[4] if len(field) > 4 else None
            val_range = field[5] if len(field) > 5 else None
            lsb, msb = bit_range

            mem_obj = reg_mem[base_tag]
            reg_val = mem_obj.read32(offset)
            val = get_bits(reg_val, lsb, msb)

            if ftype == "enum":
                disp_val = next((k for k, v in enum_map.items() if v == val), f"unknown({val})")
                extra = f"Options: {list(enum_map.keys())}"
            else:
                disp_val = str(val)
                extra = f"Range: {val_range}" if val_range else ""

            display_lines.append(((name, disp_val, extra), "field", idx))
            idx += 1

    dsu_sclk_df_src_mux_sel = get_val("dsu_sclk_df_src_mux_sel", FLAT_FIELDS)
    dsu_sclk_df_src_mux_div = get_val("dsu_sclk_df_src_mux_div", FLAT_FIELDS)
    dsu_sclk_src_t_sel = get_val("dsu_sclk_src_t_sel", FLAT_FIELDS) 

    if dsu_sclk_df_src_mux_sel == "b0pll":
        dsu_sclk_df_src_mux_clk = b0pll_freq
    elif dsu_sclk_df_src_mux_sel == "b1pll":
        dsu_sclk_df_src_mux_clk = b1pll_freq
    elif dsu_sclk_df_src_mux_sel == "lpll":
        dsu_sclk_df_src_mux_clk = lpll_freq
    elif dsu_sclk_df_src_mux_sel == "gpll":
        dsu_sclk_df_src_mux_clk = GPLL_FREQ
    else:
        dsu_sclk_df_src_mux_clk = 0

    mem_grf = Registers(GRF_DSU_BASE, 0x1000)
    dsu_pvtpll_freq = read_pvtpll_freq(mem_grf)
    mem_grf.close() 

    if dsu_sclk_src_t_sel == "dsu_src":
        sclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_sclk_df_src_mux_div + 1)
    elif dsu_sclk_src_t_sel == "PVTPLL":
        sclk_clk_freq = dsu_pvtpll_freq
    else:
        sclk_clk_freq = 0

    dsu_pclk_root_mux_sel = get_val("dsu_pclk_root_mux_sel", FLAT_FIELDS)
    dsu_pclk_root_mux_div = get_val("dsu_pclk_root_mux_div", FLAT_FIELDS)

    if dsu_pclk_root_mux_sel == "b0pll":
        dsu_pclk_root_mux_clk = b0pll_freq
    elif dsu_pclk_root_mux_sel == "b1pll":
        dsu_pclk_root_mux_clk = b1pll_freq
    elif dsu_pclk_root_mux_sel == "lpll":
        dsu_pclk_root_mux_clk = lpll_freq
    elif dsu_pclk_root_mux_sel == "gpll":
        dsu_pclk_root_mux_clk = GPLL_FREQ
    else:
        dsu_pclk_root_mux_clk = 0

    pclk_clk_freq = dsu_pclk_root_mux_clk / (dsu_pclk_root_mux_div + 1)

    dsu_aclkm_div = get_val("dsu_aclkm_div", FLAT_FIELDS)
    dsu_aclks_div = get_val("dsu_aclks_div", FLAT_FIELDS)
    dsu_aclkmp_div = get_val("dsu_aclkmp_div", FLAT_FIELDS)
    dsu_periphclk_div = get_val("dsu_periphclk_div", FLAT_FIELDS)
    dsu_cntclk_div = get_val("dsu_cntclk_div", FLAT_FIELDS)
    dsu_tsclk_div = get_val("dsu_tsclk_div", FLAT_FIELDS)
    dsu_atclk_div = get_val("dsu_atclk_div", FLAT_FIELDS)
    dsu_gicclk_div = get_val("dsu_gicclk_t_div", FLAT_FIELDS)

    aclkm_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_aclkm_div + 1)
    aclks_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_aclks_div + 1)
    aclkmp_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_aclkmp_div + 1)
    periphclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_periphclk_div + 1)
    cntclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_cntclk_div + 1)
    tsclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_tsclk_div + 1)
    atclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_atclk_div + 1)
    gicclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_gicclk_div + 1)

    freq_lines = [
        f"GPLL Frequency:           {GPLL_FREQ:.0f} MHz",
        f"B0PLL Frequency:          {b0pll_freq:.0f} MHz",
        f"B1PLL Frequency:          {b1pll_freq:.0f} MHz",
        f"LPLL Frequency:           {lpll_freq:.0f} MHz",
        f"DSU SRC MUX Freq:         {dsu_sclk_df_src_mux_clk:.0f} MHz from {dsu_sclk_df_src_mux_sel}",
        f"DSU PCLK MUX Freq:        {dsu_pclk_root_mux_clk:.0f} MHz from {dsu_pclk_root_mux_sel}",
        f"DSU PVTPLL Freq:          {dsu_pvtpll_freq:.0f} MHz",
        f"--------------",
        f"DSU SCLK Frequency:       {sclk_clk_freq:.0f} MHz from {dsu_sclk_src_t_sel}",
        f"DSU ACLK_M Frequency:     {aclkm_clk_freq:.0f} MHz",
        f"DSU ACLK_S Frequency:     {aclks_clk_freq:.0f} MHz",
        f"DSU ACLK_MP Frequency:    {aclkmp_clk_freq:.0f} MHz",
        f"DSU PERIPHCLK Frequency:  {periphclk_clk_freq:.0f} MHz",
        f"DSU CNTCLK Frequency:     {cntclk_clk_freq:.0f} MHz",
        f"DSU TSCLK Frequency:      {tsclk_clk_freq:.0f} MHz",
        f"DSU ATCLK Frequency:      {atclk_clk_freq:.0f} MHz",
        f"DSU GICCLK Frequency:     {gicclk_clk_freq:.0f} MHz",
        f"DSU PCLK Frequency:       {pclk_clk_freq:.0f} MHz",
    ]

    # Add spacer before frequency section
    display_lines.append(("", "spacer"))
    display_lines.append(("## frequency configuration ##", "section"))

    # Add frequency lines as freq entries (no index needed)
    for line in freq_lines:
        display_lines.append(((None, line, ""), "freq"))

    # Clamp scroll_offset
    if scroll_offset is None:
        scroll_offset = 0
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + visible_rows:
        scroll_offset = selected - visible_rows + 1

    # Render visible lines
    for visible_idx, (entry, etype, *rest) in enumerate(display_lines[scroll_offset:scroll_offset + visible_rows]):
        row = start_row + visible_idx 

        if etype == "spacer":
            continue
        elif etype == "section":
            stdscr.addstr(row, 0, entry, curses.color_pair(1) | curses.A_BOLD)
        elif etype == "field":
            name, disp_val, extra = entry
            field_idx = rest[0]
            highlight = curses.A_REVERSE if field_idx == selected else curses.A_NORMAL
            line = f"{name:<{FIELD_NAME_COL_WIDTH}}: {disp_val:<{VALUE_COL_WIDTH}} {extra:<{INFO_COL_WIDTH}}"
            stdscr.addstr(row, 2, line[:curses.COLS - 3], highlight)
        elif etype == "freq":
            _, line, _ = entry
            stdscr.addstr(row, 2, line[:curses.COLS - 3])

    return scroll_offset, FLAT_FIELDS

def draw_gpu_ui(stdscr, mem, selected, message, scroll_offset):
    FIELD_NAME_COL_WIDTH = 25
    VALUE_COL_WIDTH = 15
    INFO_COL_WIDTH = 35

    start_row = 2
    visible_rows = curses.LINES - start_row - 2

    # CRU & GRF Offsets
    CRU_CLKSEL_CON158 = 0x0578
    CRU_CLKSEL_CON159 = 0x057C
    CRU_CLKSEL_CON160 = 0x0584
    CRU_CLKSEL_CON161 = 0x058C
    GRF_GPU_PVTPLL_CON0_L = 0x00
    GRF_GPU_PVTPLL_CON0_H = 0x04
    GRF_GPU_PVTPLL = 0x18

    # Clocking Parameters
    SECTIONS = [
        ("## gpu pvtpll configuration ##", "GRF_GPU_BASE", [
            ("ring_length_sel", GRF_GPU_PVTPLL_CON0_L, (8, 10), "int", None, (0, 1)),
                # 0 = UDBLVT20_INV_S_4, 1 = UDBSVT20_INV_S_4
            ("ring_length_sel", GRF_GPU_PVTPLL_CON0_H, (0, 5), "int", None, (0, 63)) #number of inventers = (n+20)*2
        ]),
        ("## gpu mux configuration ##", "CRU_BASE", [
            # ("gpu_pvtpll_sel", CRU_CLKSEL_CON158, (2, 2), "enum",     // requires updating of cal_cnt register (0x8)
            #  {"clk_gpu_src": 0b0, "xin_osc0_func": 0b1}),             // no logic implemented
            ("gpu_src_div", CRU_CLKSEL_CON158, (0, 4), "int", None, (0, 31)),
            ("gpu_src_sel", CRU_CLKSEL_CON158, (5, 7), "enum",
             {"gpll": 0b000, "cpll": 0b001, "aupll": 0b010, "npll": 0b011, "spll": 0b100}),            
            ("gpu_src_mux_sel", CRU_CLKSEL_CON158, (14, 14), "enum",
             {"gpu_src": 0b0, "PVTPLL": 0b1})
        ]),
    ]

    FLAT_FIELDS = []
    for _, base_tag, fields in SECTIONS:
        mem_obj = reg_mem[base_tag]
        for entry in fields:
            name, offset, bit_range, ftype = entry[:4]
            enum_map = entry[4] if len(entry) > 4 else None
            val_range = entry[5] if len(entry) > 5 else None
            lsb, msb = bit_range
            FLAT_FIELDS.append((mem_obj, name, offset, lsb, msb, ftype, enum_map, val_range))

    if not is_gpu_safe_to_read():
        message[0] = "GPU not powered (set power_policy to always_on)"
        # Prompt user to enable always_on
        display_lines = []
        idx = 0
        display_lines.append((("GPU power_policy is not 'always_on'. Enable it now? (y/n):", "", ""), "field", idx))
        idx += 1

        # Draw prompt before asking
        row = 3
        for line, kind, _ in display_lines:
            if kind == "field":
                name, val, extra = line
                stdscr.addstr(row, 2, f"{name:<40}{val:<15}{extra}")
            row += 1
        stdscr.refresh()

        c = stdscr.getch()
        if c in (ord('y'), ord('Y')):
            if set_gpu_power_policy_always_on():
                message[0] = "GPU power_policy set to always_on."
            else:
                message[0] = "Failed to set power_policy. Root permissions needed?"
        else:
            message[0] = "GPU remains not powered."

        display_lines.append(((message[0], "", ""), "field", idx))
        idx += 1

        # Draw result message
        stdscr.addstr(row, 2, f"{message[0]}")
        stdscr.refresh()

        return scroll_offset, []

    def read_pvtpll_freq(mem_grf):
        freq_mhz = mem_grf.read32(GRF_GPU_PVTPLL)
        return freq_mhz

    # Gather fields to display
    display_lines = []
    idx = 0
    for section_title, base_tag, fields in SECTIONS:
        display_lines.append(("", "spacer"))
        display_lines.append((section_title, "section"))
        for field in fields:
            name, offset, bit_range, ftype = field[:4]
            enum_map = field[4] if len(field) > 4 else None
            val_range = field[5] if len(field) > 5 else None
            lsb, msb = bit_range

            mem_obj = reg_mem[base_tag]
            reg_val = mem_obj.read32(offset)
            val = get_bits(reg_val, lsb, msb)

            if ftype == "enum":
                disp_val = next((k for k, v in enum_map.items() if v == val), f"unknown({val})")
                extra = f"Options: {list(enum_map.keys())}"
            else:
                disp_val = str(val)
                extra = f"Range: {val_range}" if val_range else ""

            display_lines.append(((name, disp_val, extra), "field", idx))
            idx += 1

    mem_grf = Registers(GRF_GPU_BASE, 0x1000)
    gpu_pvtpll_freq = read_pvtpll_freq(mem_grf)
    mem_grf.close() 

    gpu_src_sel = get_val("gpu_src_sel", FLAT_FIELDS)
    gpu_src_div = get_val("gpu_src_div", FLAT_FIELDS)
    gpu_src_mux_sel = get_val("gpu_src_mux_sel", FLAT_FIELDS)

    if gpu_src_sel == "aupll":
        gpu_src_mux_clk = AUPLL_FREQ / (gpu_src_div + 1)
    elif gpu_src_sel == "cpll":
        gpu_src_mux_clk = CPLL_FREQ / (gpu_src_div + 1)
    elif gpu_src_sel == "gpll":
        gpu_src_mux_clk = GPLL_FREQ / (gpu_src_div + 1)
    elif gpu_src_sel == "npll":
        gpu_src_mux_clk = NPLL_FREQ / (gpu_src_div + 1)
    elif gpu_src_sel == "spll":
        gpu_src_mux_clk = SPLL_FREQ / (gpu_src_div + 1)
    else:
        gpu_src_mux_clk = 0

    if gpu_src_mux_sel == "gpu_src":
        gpu_clk_freq = gpu_src_mux_clk
    elif gpu_src_mux_sel == "PVTPLL":
        gpu_clk_freq = gpu_pvtpll_freq
    else:
        gpu_clk_freq = 0

    freq_lines = [
        f"AUPLL Frequency:          {AUPLL_FREQ:.0f} MHz",
        f"CPLL Frequency:           {CPLL_FREQ:.0f} MHz",
        f"GPLL Frequency:           {GPLL_FREQ:.0f} MHz",
        f"NPLL Frequency:           {NPLL_FREQ:.0f} MHz",
        f"SPLL Frequency:           {SPLL_FREQ:.0f} MHz",
        f"GPU SRC MUX Freq:         {gpu_src_mux_clk:.0f} MHz from {gpu_src_sel}",
        f"GPU PVTPLL Freq:          {gpu_pvtpll_freq:.0f} MHz",
        f"--------------",
        f"GPU Frequency:            {gpu_clk_freq:.0f} MHz from {gpu_src_mux_sel}",        
    ]

    # Add spacer before frequency section
    display_lines.append(("", "spacer"))
    display_lines.append(("## frequency configuration ##", "section"))

    # Add frequency lines as freq entries (no index needed)
    for line in freq_lines:
        display_lines.append(((None, line, ""), "freq"))

    # Clamp scroll_offset
    if scroll_offset is None:
        scroll_offset = 0
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + visible_rows:
        scroll_offset = selected - visible_rows + 1

    # Render visible lines
    for visible_idx, (entry, etype, *rest) in enumerate(display_lines[scroll_offset:scroll_offset + visible_rows]):
        row = start_row + visible_idx 

        if etype == "spacer":
            continue
        elif etype == "section":
            stdscr.addstr(row, 0, entry, curses.color_pair(1) | curses.A_BOLD)
        elif etype == "field":
            name, disp_val, extra = entry
            field_idx = rest[0]
            highlight = curses.A_REVERSE if field_idx == selected else curses.A_NORMAL
            line = f"{name:<{FIELD_NAME_COL_WIDTH}}: {disp_val:<{VALUE_COL_WIDTH}} {extra:<{INFO_COL_WIDTH}}"
            stdscr.addstr(row, 2, line[:curses.COLS - 3], highlight)
        elif etype == "freq":
            _, line, _ = entry
            stdscr.addstr(row, 2, line[:curses.COLS - 3])

    return scroll_offset, FLAT_FIELDS

def draw_npu_ui(stdscr, mem, selected, message, scroll_offset):
    FIELD_NAME_COL_WIDTH = 25
    VALUE_COL_WIDTH = 15
    INFO_COL_WIDTH = 35

    start_row = 2
    visible_rows = curses.LINES - start_row - 2

    # CRU & GRF Offsets
    CRU_CLKSEL_CON73 = 0x0424
    CRU_CLKSEL_CON74 = 0x0428
    GRF_NPU_PVTPLL_CON0_L = 0x0C
    GRF_NPU_PVTPLL_CON0_H = 0x10
    GRF_NPU_PVTPLL = 0x24

    #def read_pvtpll_freq(mem_grf):
    #    freq_mhz = mem_grf.read32(GRF_NPU_PVTPLL)
    #    return freq_mhz

    # Clocking Parameters
    SECTIONS = [
        #("## npu pvtpll configuration ##", "GRF_NPU_BASE", [
        #    ("ring_length_sel", GRF_NPU_PVTPLL_CON0_L, (8, 10), "int", None, (0, 1)),
        #        # 0 = UDBLVT20_INV_S_4, 1 = UDBSVT20_INV_S_4
        #    ("ring_length_sel", GRF_NPU_PVTPLL_CON0_H, (0, 5), "int", None, (0, 63)), #number of inventers = (n+20)*2
        #]),
        ("## npu mux configuration ##", "CRU_BASE", [
            ("rknn_dsu0_src_sel", CRU_CLKSEL_CON73, (7, 9), "enum",
             {"gpll": 0b000, "cpll": 0b001, "aupll": 0b010, "npll": 0b011, "spll": 0b100}),
            ("rknn_dsu0_src_div", CRU_CLKSEL_CON73, (2, 6), "int", None, (0, 31)),
            ("rknn_dsu0_mux_sel", CRU_CLKSEL_CON74, (0, 0), "enum",
             {"dsu0_src": 0b0, "PVTPLL": 0b1}),
            # ("npu_pvtpll_sel", CRU_CLKSEL_CON74, (4, 4), "enum",      // requires updating of cal_cnt register (0x14)
            #  {"dsu0_src": 0b0, "xin_osc0_func": 0b1}),                // no logic implemented
            ("npu_cm0_rtc_div", CRU_CLKSEL_CON74, (7, 11), "int", None, (0, 31)),
        ]),
    ]

    FLAT_FIELDS = []
    for _, base_tag, fields in SECTIONS:
        mem_obj = reg_mem[base_tag]
        for entry in fields:
            name, offset, bit_range, ftype = entry[:4]
            enum_map = entry[4] if len(entry) > 4 else None
            val_range = entry[5] if len(entry) > 5 else None
            lsb, msb = bit_range
            FLAT_FIELDS.append((mem_obj, name, offset, lsb, msb, ftype, enum_map, val_range))

    # Gather fields to display
    display_lines = []
    idx = 0
    for section_title, base_tag, fields in SECTIONS:
        display_lines.append(("", "spacer"))
        display_lines.append((section_title, "section"))
        for field in fields:
            name, offset, bit_range, ftype = field[:4]
            enum_map = field[4] if len(field) > 4 else None
            val_range = field[5] if len(field) > 5 else None
            lsb, msb = bit_range

            mem_obj = reg_mem[base_tag]
            reg_val = mem_obj.read32(offset)
            val = get_bits(reg_val, lsb, msb)

            if ftype == "enum":
                disp_val = next((k for k, v in enum_map.items() if v == val), f"unknown({val})")
                extra = f"Options: {list(enum_map.keys())}"
            else:
                disp_val = str(val)
                extra = f"Range: {val_range}" if val_range else ""

            display_lines.append(((name, disp_val, extra), "field", idx))
            idx += 1

    #mem_grf = Registers(GRF_NPU_BASE, 0x1000)
    #npu_pvtpll_freq = read_pvtpll_freq(mem_grf)
    #mem_grf.close() 

    dsu0_src_sel = get_val("rknn_dsu0_src_sel", FLAT_FIELDS)
    dsu0_src_div = get_val("rknn_dsu0_src_div", FLAT_FIELDS)
    dsu0_src_mux_sel = get_val("rknn_dsu0_mux_sel", FLAT_FIELDS)

    if dsu0_src_sel == "aupll":
        dsu0_src_mux_clk = AUPLL_FREQ / (dsu0_src_div + 1)
    elif dsu0_src_sel == "cpll":
        dsu0_src_mux_clk = CPLL_FREQ / (dsu0_src_div + 1)
    elif dsu0_src_sel == "gpll":
        dsu0_src_mux_clk = GPLL_FREQ / (dsu0_src_div + 1)
    elif dsu0_src_sel == "npll":
        dsu0_src_mux_clk = NPLL_FREQ / (dsu0_src_div + 1)
    elif dsu0_src_sel == "spll":
        dsu0_src_mux_clk = SPLL_FREQ / (dsu0_src_div + 1)
    else:
        dsu0_src_mux_clk = 0

    if dsu0_src_mux_sel == "dsu0_src":
        npu_clk_freq = dsu0_src_mux_clk
    elif dsu0_src_mux_sel == "PVTPLL":
        npu_clk_freq = -1 # replace with npu_clk_freq = npu_pvtpll_freq when pvtpll can be read
    else:
        npu_clk_freq = 0

    freq_lines = [
        f"AUPLL Frequency:          {AUPLL_FREQ:.0f} MHz",
        f"CPLL Frequency:           {CPLL_FREQ:.0f} MHz",
        f"GPLL Frequency:           {GPLL_FREQ:.0f} MHz",
        f"NPLL Frequency:           {NPLL_FREQ:.0f} MHz",
        f"SPLL Frequency:           {SPLL_FREQ:.0f} MHz",
        f"NPU SRC MUX Freq:         {dsu0_src_mux_clk:.0f} MHz from {dsu0_src_sel}",
        #f"NPU PVTPLL Freq:          {npu_pvtpll_freq:.0f} MHz",
        f"--------------",
        f"NPU Frequency:            {npu_clk_freq:.0f} MHz from {dsu0_src_mux_sel}",        
    ]

    # Add spacer before frequency section
    display_lines.append(("", "spacer"))
    display_lines.append(("## frequency configuration ##", "section"))

    # Add frequency lines as freq entries (no index needed)
    for line in freq_lines:
        display_lines.append(((None, line, ""), "freq"))

    # Clamp scroll_offset
    if scroll_offset is None:
        scroll_offset = 0
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + visible_rows:
        scroll_offset = selected - visible_rows + 1

    # Render visible lines
    for visible_idx, (entry, etype, *rest) in enumerate(display_lines[scroll_offset:scroll_offset + visible_rows]):
        row = start_row + visible_idx 

        if etype == "spacer":
            continue
        elif etype == "section":
            stdscr.addstr(row, 0, entry, curses.color_pair(1) | curses.A_BOLD)
        elif etype == "field":
            name, disp_val, extra = entry
            field_idx = rest[0]
            highlight = curses.A_REVERSE if field_idx == selected else curses.A_NORMAL
            line = f"{name:<{FIELD_NAME_COL_WIDTH}}: {disp_val:<{VALUE_COL_WIDTH}} {extra:<{INFO_COL_WIDTH}}"
            stdscr.addstr(row, 2, line[:curses.COLS - 3], highlight)
        elif etype == "freq":
            _, line, _ = entry
            stdscr.addstr(row, 2, line[:curses.COLS - 3])

    return scroll_offset, FLAT_FIELDS

def draw_dram_ui(stdscr, mem, selected, message, scroll_offset):
    FIELD_NAME_COL_WIDTH = 25
    VALUE_COL_WIDTH = 15
    INFO_COL_WIDTH = 35

    start_row = 2
    visible_rows = curses.LINES - start_row - 2

    # CRU & GRF Offsets
    DDR0CRU_D0APLL_CON0 = 0x0000
    DDR0CRU_D0APLL_CON1 = 0x0004
    DDR0CRU_D0APLL_CON2 = 0x0008
    DDR0CRU_D0APLL_CON6 = 0x0018
    DDR0CRU_D0BPLL_CON0 = 0x0020
    DDR0CRU_D0BPLL_CON1 = 0x0024
    DDR0CRU_D0BPLL_CON2 = 0x0028
    DDR0CRU_D0BPLL_CON6 = 0x0038
    DDR0CRU_CLKSEL_CON00 = 0x0300
    
    DDR1CRU_D1APLL_CON0 = 0x0000
    DDR1CRU_D1APLL_CON1 = 0x0004
    DDR1CRU_D1APLL_CON2 = 0x0008
    DDR1CRU_D1APLL_CON6 = 0x0018
    DDR1CRU_D1BPLL_CON0 = 0x0020
    DDR1CRU_D1BPLL_CON1 = 0x0024
    DDR1CRU_D1BPLL_CON2 = 0x0028
    DDR1CRU_D1BPLL_CON6 = 0x0038
    DDR1CRU_CLKSEL_CON00 = 0x0300

    DDR2CRU_D2APLL_CON0 = 0x0000
    DDR2CRU_D2APLL_CON1 = 0x0004
    DDR2CRU_D2APLL_CON2 = 0x0008
    DDR2CRU_D2APLL_CON6 = 0x0018
    DDR2CRU_D2BPLL_CON0 = 0x0020
    DDR2CRU_D2BPLL_CON1 = 0x0024
    DDR2CRU_D2BPLL_CON2 = 0x0028
    DDR2CRU_D2BPLL_CON6 = 0x0038
    DDR2CRU_CLKSEL_CON00 = 0x0300

    DDR3CRU_D3APLL_CON0 = 0x0000
    DDR3CRU_D3APLL_CON1 = 0x0004
    DDR3CRU_D3APLL_CON2 = 0x0008
    DDR3CRU_D3APLL_CON6 = 0x0018
    DDR3CRU_D3BPLL_CON0 = 0x0020
    DDR3CRU_D3BPLL_CON1 = 0x0024
    DDR3CRU_D3BPLL_CON2 = 0x0028
    DDR3CRU_D3BPLL_CON6 = 0x0038
    DDR3CRU_CLKSEL_CON00 = 0x0300

    # Clocking Parameters
    SECTIONS = [
        ("## d0a pll configuration ##", "CRU_DDRPHY0_BASE", [
            ("m_d0apll", DDR0CRU_D0APLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d0apll", DDR0CRU_D0APLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d0apll", DDR0CRU_D0APLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d0apll", DDR0CRU_D0APLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d0apll_pll_reset", DDR0CRU_D0APLL_CON1, (13, 13), "int", None, (0, 1)),
            ("d0apll_lock", DDR0CRU_D0APLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## d0b pll configuration ##", "CRU_DDRPHY0_BASE", [
            ("m_d0bpll", DDR0CRU_D0BPLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d0bpll", DDR0CRU_D0BPLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d0bpll", DDR0CRU_D0BPLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d0bpll", DDR0CRU_D0BPLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d0bpll_pll_reset", DDR0CRU_D0BPLL_CON0, (13, 13), "int", None, (0, 1)),
            ("d0bpll_lock", DDR0CRU_D0BPLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## d1a pll configuration ##", "CRU_DDRPHY1_BASE", [
            ("m_d1apll", DDR1CRU_D1APLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d1apll", DDR1CRU_D1APLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d1apll", DDR1CRU_D1APLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d1apll", DDR1CRU_D1APLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d1apll_pll_reset", DDR1CRU_D1APLL_CON1, (13, 13), "int", None, (0, 1)),
            ("d1apll_lock", DDR1CRU_D1APLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## d1b pll configuration ##", "CRU_DDRPHY1_BASE", [
            ("m_d1bpll", DDR1CRU_D1BPLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d1bpll", DDR1CRU_D1BPLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d1bpll", DDR1CRU_D1BPLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d1bpll", DDR1CRU_D1BPLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d1bpll_pll_reset", DDR1CRU_D1BPLL_CON0, (13, 13), "int", None, (0, 1)),
            ("d1bpll_lock", DDR1CRU_D1BPLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## d2a pll configuration ##", "CRU_DDRPHY2_BASE", [
            ("m_d2apll", DDR2CRU_D2APLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d2apll", DDR2CRU_D2APLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d2apll", DDR2CRU_D2APLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d2apll", DDR2CRU_D2APLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d2apll_pll_reset", DDR2CRU_D2APLL_CON1, (13, 13), "int", None, (0, 1)),
            ("d2apll_lock", DDR2CRU_D2APLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## d2b pll configuration ##", "CRU_DDRPHY2_BASE", [
            ("m_d2bpll", DDR2CRU_D2BPLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d2bpll", DDR2CRU_D2BPLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d2bpll", DDR2CRU_D2BPLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d2bpll", DDR2CRU_D2BPLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d2bpll_pll_reset", DDR2CRU_D2BPLL_CON0, (13, 13), "int", None, (0, 1)),
            ("d2bpll_lock", DDR2CRU_D2BPLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## d3a pll configuration ##", "CRU_DDRPHY3_BASE", [
            ("m_d3apll", DDR3CRU_D3APLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d3apll", DDR3CRU_D3APLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d3apll", DDR3CRU_D3APLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d3apll", DDR3CRU_D3APLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d3apll_pll_reset", DDR3CRU_D3APLL_CON1, (13, 13), "int", None, (0, 1)),
            ("d3apll_lock", DDR3CRU_D3APLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## d3b pll configuration ##", "CRU_DDRPHY3_BASE", [
            ("m_d3bpll", DDR3CRU_D3BPLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_d3bpll", DDR3CRU_D3BPLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_d3bpll", DDR3CRU_D3BPLL_CON1, (6, 8), "int", None, (0, 6)),
            ("k_d3bpll", DDR3CRU_D3BPLL_CON2, (0, 15), "int", None, (0, 1023)), 
            ("d3bpll_pll_reset", DDR3CRU_D3BPLL_CON0, (13, 13), "int", None, (0, 1)),
            ("d3bpll_lock", DDR3CRU_D3BPLL_CON6, (15, 15), "int", None, (0, 1))
        ]),
        ("## ddr0clk configuration ##", "CRU_DDRPHY0_BASE", [
            ("ddrphy2x_ch0_sel", DDR0CRU_CLKSEL_CON00, (0, 0), "enum",
             {"d0apll": 0b00, "d0bpll": 0b01})
        ]),
        ("## ddr1clk configuration ##", "CRU_DDRPHY1_BASE", [
            ("ddrphy2x_ch1_sel", DDR1CRU_CLKSEL_CON00, (0, 0), "enum",
             {"d1apll": 0b00, "d1bpll": 0b01})
        ]),
        ("## ddr2clk configuration ##", "CRU_DDRPHY2_BASE", [
            ("ddrphy2x_ch2_sel", DDR2CRU_CLKSEL_CON00, (0, 0), "enum",
             {"d2apll": 0b00, "d3bpll": 0b01})
        ]),
        ("## ddr3clk configuration ##", "CRU_DDRPHY3_BASE", [
            ("ddrphy2x_ch3_sel", DDR3CRU_CLKSEL_CON00, (0, 0), "enum",
             {"d3apll": 0b00, "d3bpll": 0b01}),
        ]),
    ]

    FLAT_FIELDS = []
    for _, base_tag, fields in SECTIONS:
        mem_obj = reg_mem[base_tag]
        for entry in fields:
            name, offset, bit_range, ftype = entry[:4]
            enum_map = entry[4] if len(entry) > 4 else None
            val_range = entry[5] if len(entry) > 5 else None
            lsb, msb = bit_range
            FLAT_FIELDS.append((mem_obj, name, offset, lsb, msb, ftype, enum_map, val_range))

    # Gather fields to display
    display_lines = []
    idx = 0
    for section_title, base_tag, fields in SECTIONS:
        display_lines.append(("", "spacer"))
        display_lines.append((section_title, "section"))
        for field in fields:
            name, offset, bit_range, ftype = field[:4]
            enum_map = field[4] if len(field) > 4 else None
            val_range = field[5] if len(field) > 5 else None
            lsb, msb = bit_range

            mem_obj = reg_mem[base_tag]
            reg_val = mem_obj.read32(offset)
            val = get_bits(reg_val, lsb, msb)

            if ftype == "enum":
                disp_val = next((k for k, v in enum_map.items() if v == val), f"unknown({val})")
                extra = f"Options: {list(enum_map.keys())}"
            else:
                disp_val = str(val)
                extra = f"Range: {val_range}" if val_range else ""

            display_lines.append(((name, disp_val, extra), "field", idx))
            idx += 1

    m_d0a = get_val("m_d0apll", FLAT_FIELDS)
    p_d0a = get_val("p_d0apll", FLAT_FIELDS)
    s_d0a = get_val("s_d0apll", FLAT_FIELDS)
    k_d0a = get_val("k_d0apll", FLAT_FIELDS)
    d0apll = (XIN_OSC0_FREQ * ((m_d0a + (k_d0a / 2^16)) / (p_d0a * 2^s_d0a)))

    m_d0b = get_val("m_d0bpll", FLAT_FIELDS)
    p_d0b = get_val("p_d0bpll", FLAT_FIELDS)
    s_d0b = get_val("s_d0bpll", FLAT_FIELDS)
    k_d0b = get_val("k_d0bpll", FLAT_FIELDS)
    d0bpll = (XIN_OSC0_FREQ * ((m_d0b + (k_d0b / 2^16)) / (p_d0b * 2^s_d0b)))

    m_d1a = get_val("m_d1apll", FLAT_FIELDS)
    p_d1a = get_val("p_d1apll", FLAT_FIELDS)
    s_d1a = get_val("s_d1apll", FLAT_FIELDS)
    k_d1a = get_val("k_d1apll", FLAT_FIELDS)
    d1apll = (XIN_OSC0_FREQ * ((m_d1a + (k_d1a / 2^16)) / (p_d1a * 2^s_d1a)))

    m_d1b = get_val("m_d1bpll", FLAT_FIELDS)
    p_d1b = get_val("p_d1bpll", FLAT_FIELDS)
    s_d1b = get_val("s_d1bpll", FLAT_FIELDS)
    k_d1b = get_val("k_d1bpll", FLAT_FIELDS)
    d1bpll = (XIN_OSC0_FREQ * ((m_d1b + (k_d1b / 2^16)) / (p_d1b * 2^s_d1b)))

    m_d2a = get_val("m_d2apll", FLAT_FIELDS)
    p_d2a = get_val("p_d2apll", FLAT_FIELDS)
    s_d2a = get_val("s_d2apll", FLAT_FIELDS)
    k_d2a = get_val("k_d2apll", FLAT_FIELDS)
    d2apll = (XIN_OSC0_FREQ * ((m_d2a + (k_d2a / 2^16)) / (p_d2a * 2^s_d2a)))

    m_d2b = get_val("m_d2bpll", FLAT_FIELDS)
    p_d2b = get_val("p_d2bpll", FLAT_FIELDS)
    s_d2b = get_val("s_d2bpll", FLAT_FIELDS)
    k_d2b = get_val("k_d2bpll", FLAT_FIELDS)
    d2bpll = (XIN_OSC0_FREQ * ((m_d2b + (k_d2b / 2^16)) / (p_d2b * 2^s_d2b)))

    m_d3a = get_val("m_d3apll", FLAT_FIELDS)
    p_d3a = get_val("p_d3apll", FLAT_FIELDS)
    s_d3a = get_val("s_d3apll", FLAT_FIELDS)
    k_d3a = get_val("k_d3apll", FLAT_FIELDS)
    d3apll = (XIN_OSC0_FREQ * ((m_d3a + (k_d3a / 2^16)) / (p_d3a * 2^s_d3a)))

    m_d3b = get_val("m_d3bpll", FLAT_FIELDS)
    p_d3b = get_val("p_d3bpll", FLAT_FIELDS)
    s_d3b = get_val("s_d3bpll", FLAT_FIELDS)
    k_d3b = get_val("k_d3bpll", FLAT_FIELDS)
    d3bpll = (XIN_OSC0_FREQ * ((m_d3b + (k_d3b / 2^16)) / (p_d3b * 2^s_d3b)))

    ddrphy2x_ch0_sel = get_val("ddrphy2x_ch0_sel", FLAT_FIELDS)
    ddrphy2x_ch1_sel = get_val("ddrphy2x_ch1_sel", FLAT_FIELDS)
    ddrphy2x_ch2_sel = get_val("ddrphy2x_ch2_sel", FLAT_FIELDS)
    ddrphy2x_ch3_sel = get_val("ddrphy2x_ch3_sel", FLAT_FIELDS)

    if ddrphy2x_ch0_sel == "d0apll":
        ddrphy2x_ch0_clk_freq = d0apll
    elif ddrphy2x_ch0_sel == "d0bpll":
        ddrphy2x_ch0_clk_freq = d0bpll
    else:
        ddrphy2x_ch0_clk_freq = 0

    if ddrphy2x_ch1_sel == "d1apll":
        ddrphy2x_ch1_clk_freq = d1apll
    elif ddrphy2x_ch1_sel == "d1bpll":
        ddrphy2x_ch1_clk_freq = d1bpll
    else:
        ddrphy2x_ch1_clk_freq = 0

    if ddrphy2x_ch2_sel == "d2apll":
        ddrphy2x_ch2_clk_freq = d2apll
    elif ddrphy2x_ch2_sel == "d2bpll":
        ddrphy2x_ch2_clk_freq = d2bpll
    else:
        ddrphy2x_ch2_clk_freq = 0

    if ddrphy2x_ch3_sel == "d2apll":
        ddrphy2x_ch3_clk_freq = d2apll
    elif ddrphy2x_ch3_sel == "d2bpll":
        ddrphy2x_ch3_clk_freq = d2bpll
    else:
        ddrphy2x_ch3_clk_freq = 0

    freq_lines = [
        f"DDR D0A PLL Frequency:    {d0apll:.0f} MHz",
        f"DDR D0B PLL Frequency:    {d0bpll:.0f} MHz",
        f"DDR D1A PLL Frequency:    {d1apll:.0f} MHz",
        f"DDR D1B PLL Frequency:    {d1bpll:.0f} MHz",
        f"DDR D2A PLL Frequency:    {d2apll:.0f} MHz",
        f"DDR D2B PLL Frequency:    {d2bpll:.0f} MHz",
        f"DDR D3A PLL Frequency:    {d3apll:.0f} MHz",
        f"DDR D3B PLL Frequency:    {d3bpll:.0f} MHz",
        f"--------------",
        f"DDR Channel0 Frequency:   {ddrphy2x_ch0_clk_freq:.0f} MHz from {ddrphy2x_ch0_sel}",        
        f"DDR Channel1 Frequency:   {ddrphy2x_ch1_clk_freq:.0f} MHz from {ddrphy2x_ch1_sel}",        
        f"DDR Channel2 Frequency:   {ddrphy2x_ch2_clk_freq:.0f} MHz from {ddrphy2x_ch2_sel}",        
        f"DDR Channel3 Frequency:   {ddrphy2x_ch3_clk_freq:.0f} MHz from {ddrphy2x_ch3_sel}",        
    ]

    # Add spacer before frequency section
    display_lines.append(("", "spacer"))
    display_lines.append(("## frequency configuration ##", "section"))

    # Add frequency lines as freq entries (no index needed)
    for line in freq_lines:
        display_lines.append(((None, line, ""), "freq"))

    # Clamp scroll_offset
    if scroll_offset is None:
        scroll_offset = 0
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + visible_rows:
        scroll_offset = selected - visible_rows + 1

    # Render visible lines
    for visible_idx, (entry, etype, *rest) in enumerate(display_lines[scroll_offset:scroll_offset + visible_rows]):
        row = start_row + visible_idx 

        if etype == "spacer":
            continue
        elif etype == "section":
            stdscr.addstr(row, 0, entry, curses.color_pair(1) | curses.A_BOLD)
        elif etype == "field":
            name, disp_val, extra = entry
            field_idx = rest[0]
            highlight = curses.A_REVERSE if field_idx == selected else curses.A_NORMAL
            line = f"{name:<{FIELD_NAME_COL_WIDTH}}: {disp_val:<{VALUE_COL_WIDTH}} {extra:<{INFO_COL_WIDTH}}"
            stdscr.addstr(row, 2, line[:curses.COLS - 3], highlight)
        elif etype == "freq":
            _, line, _ = entry
            stdscr.addstr(row, 2, line[:curses.COLS - 3])

    return scroll_offset, FLAT_FIELDS

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)  # Yellow text, default background (for title & section header)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # Tab normal: yellow on black
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # Tab selected: black on yellow

def tui(stdscr):
    curses.curs_set(0)
    init_colors()

    # Message placeholder
    message = [""]

    # Tab setup
    tabs = ["General Info", "BigCore0", "BigCore1", "LittleCore", "DSU", "GPU", "NPU", "DRAM"]
    current_tab = 0  # Index of the active tab
    NUM_TABS = len(tabs)

    scroll_offsets = [0] * NUM_TABS
    selected_idx = [0] * NUM_TABS

    mem_map = {
        1: Registers(CRU_BIGCORE0_BASE, REG_SIZE),  # Bigcore0
        1: Registers(GRF_BIGCORE0_BASE, REG_SIZE),  # Bigcore0
        2: Registers(CRU_BIGCORE1_BASE, REG_SIZE),  # Bigcore1
        2: Registers(GRF_BIGCORE1_BASE, REG_SIZE),  # Bigcore1
        3: Registers(CRU_DSU_BASE, REG_SIZE),       # Littlecore
        3: Registers(GRF_LITCORE_BASE, REG_SIZE),   # Littlecore
        4: Registers(CRU_DSU_BASE, REG_SIZE),       # DSU
        4: Registers(GRF_DSU_BASE, REG_SIZE),       # DSU
        5: Registers(CRU_BASE, REG_SIZE),           # GPU
        5: Registers(GRF_GPU_BASE, REG_SIZE),       # GPU
        6: Registers(GRF_NPU_BASE, REG_SIZE),       # NPU
        7: Registers(CRU_DDRPHY0_BASE, REG_SIZE),   # DRAM
        7: Registers(CRU_DDRPHY1_BASE, REG_SIZE),   # DRAM
        7: Registers(CRU_DDRPHY2_BASE, REG_SIZE),   # DRAM
        7: Registers(CRU_DDRPHY3_BASE, REG_SIZE),   # DRAM
    }

    FLAT_FIELDS_BY_TAB = {
        1: [],  # Bigcore0
        2: [],  # Bigcore1
        3: [],  # Littlecore
        4: [],  # DSU
        5: [],  # GPU
        6: [],  # NPU
        7: [],  # DRAM
    }

    MIN_ROWS, MIN_COLS = 34, 80

    try:
        while True:
            stdscr.clear()
            height, width = stdscr.getmaxyx()

            if height < MIN_ROWS or width < MIN_COLS:
                warning = f"Terminal too small! Min size: {MIN_COLS}x{MIN_ROWS}"
                stdscr.addstr(height // 2, max((width - len(warning)) // 2, 0), warning, curses.A_BOLD)
                stdscr.refresh()
                time.sleep(1)
                continue            

            # Get tab-specific state
            selected = selected_idx[current_tab]
            scroll_offset = scroll_offsets[current_tab]

            # Draw header and tab bar
            draw_header(stdscr, current_tab, tabs)

            # Get mem object for current tab if exists
            mem = mem_map.get(current_tab)
            
            # Draw tab content and update scroll
            new_scroll_offset = draw_tab_content(
                stdscr,
                current_tab,
                mem,
                selected,
                scroll_offset,
                message
            )

            # Save new scroll offset
            if current_tab == 1:
                scroll_offsets[current_tab], FLAT_FIELDS_BY_TAB[current_tab], b0pll_freq = draw_bigcore0_ui(stdscr, mem, selected, message, scroll_offset)
            elif current_tab == 2:
                scroll_offsets[current_tab], FLAT_FIELDS_BY_TAB[current_tab], b1pll_freq = draw_bigcore1_ui(stdscr, mem, selected, message, scroll_offset)
            elif current_tab == 3:
                scroll_offsets[current_tab], FLAT_FIELDS_BY_TAB[current_tab], lpll_freq = draw_littlecore_ui(stdscr, mem, selected, message, scroll_offset)
            elif current_tab == 4:
                scroll_offsets[current_tab], FLAT_FIELDS_BY_TAB[current_tab] = draw_dsu_ui(stdscr, mem, selected, message, scroll_offset, lpll_freq=0, b0pll_freq=0, b1pll_freq=0)
            elif current_tab == 5:
                scroll_offsets[current_tab], FLAT_FIELDS_BY_TAB[current_tab] = draw_gpu_ui(stdscr, mem, selected, message, scroll_offset)
            elif current_tab == 6:
                scroll_offsets[current_tab], FLAT_FIELDS_BY_TAB[current_tab] = draw_npu_ui(stdscr, mem, selected, message, scroll_offset)
            #elif current_tab == 7:
            #    scroll_offsets[current_tab], FLAT_FIELDS_BY_TAB[current_tab] = draw_dram_ui(stdscr, mem, selected, message, scroll_offset) // devmem read bus errors cause system freeze
            elif current_tab == 7:
                draw_coming_soon(stdscr, current_tab, offset=3)

            stdscr.move(curses.LINES - 1, 0) 
            stdscr.clrtoeol() 

            stdscr.addstr(curses.LINES - 1, 0, message[0])

            stdscr.refresh()
            key = stdscr.getch()

            if key == ord('q'):
                break

            elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                if key == curses.KEY_LEFT:
                    current_tab = (current_tab - 1) % len(tabs)
                else:
                    current_tab = (current_tab + 1) % len(tabs)
                selected = 0
                scroll_offset = 0
                message[0] = ""

            elif current_tab in FLAT_FIELDS_BY_TAB:
                fields = FLAT_FIELDS_BY_TAB[current_tab]
                if not fields:
                    continue
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(fields)
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(fields)

                visible_lines = curses.LINES - 3
                
                if scroll_offset is None:
                    scroll_offset = 0
                if selected < scroll_offset:
                    scroll_offset = selected
                elif selected >= scroll_offset + visible_lines:
                    scroll_offset = selected - visible_lines + 1
                elif key == ord('\n'):
                    curses.echo()
                    prompt_row = curses.LINES - 2
                    if prompt_row > 0:
                        field_label = fields[selected][1]
                        stdscr.addstr(prompt_row, 0, f"Enter new value for {field_label}: ")
                        stdscr.clrtoeol()
                        stdscr.refresh()
                        try:
                            value = stdscr.getstr().decode('utf-8').strip()
                            current_flat_fields = FLAT_FIELDS_BY_TAB.get(current_tab, [])
                            success = write_field(mem, fields[selected], value, message, current_flat_fields,
                                bigcore0_mux_clk=bigcore0_mux_clk)
                            if not success:
                                # Show error in message box or log
                                pass
                        except Exception as e:
                            message[0] = f"Error: {e}"
                        curses.noecho()

            # Save updated selected and scroll_offset for current tab
            selected_idx[current_tab] = selected
            scroll_offsets[current_tab] = new_scroll_offset

    except KeyboardInterrupt:
        stdscr.clear()
        exit_msg = "Exiting RK3588 OC Tool. Thanks for playing! Press any key to exit."
        stdscr.addstr(curses.LINES // 2, max((curses.COLS - len(exit_msg)) // 2, 0), exit_msg, curses.A_BOLD)
        stdscr.refresh()
        stdscr.getch()

    finally:
        for mem in mem_map.values():
            mem.close()
        curses.nocbreak()
        curses.echo()
        curses.endwin()

if __name__ == "__main__":
    curses.wrapper(tui)
