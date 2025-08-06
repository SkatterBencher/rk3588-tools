#!/usr/bin/env python3
import os
import time
import glob
import argparse
import datetime
import csv
import re
import struct
import mmap
from collections import OrderedDict
import threading
import curses

# === UTILITY FUNCTIONS ===
def read_file(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except:
        return None

def show_header(title):
    print(f"\n## {title} ##")

# === BASIC CLOCK FREQUENCIES ===
CLK_ORDERED = OrderedDict([
    ("armclk_l",            "armclk_A55_0-3"),
    ("armclk_b01",          "armclk_A76_0-1"),      
    ("armclk_b23",          "armclk_A75_2-3"),
    ("scmi_clk_cpul",       "scmi_clk_A55_0-3"),
    ("scmi_clk_cpub01",     "scmi_clk_A76_0-1"),
    ("scmi_clk_cpub23",     "scmi_clk_A76_2-3"),
    ("scmi_clk_dsu",        "scmi_clk_DSU"),
    ("scmi_clk_ddr",        "scmi_clk_DMC"),
    ("scmi_clk_npu",        "scmi_clk_NPU"),
    ("scmi_clk_gpu",        "scmi_clk_GPU"),
    ("clk_gpu",             "clk_GPU"),
    ("clk_gpu_stacks",      "clk_GPU_STACKS"),
    ("clk_gpu_coregroup",   "clk_GPU_COREGROUP"),
    ("dclk_vop3",           "dclk_VOP3"),
    ("dclk_vop2",           "dclk_VOP2"),
    ("dclk_vop1",           "dclk_VOP1"),
])

def get_clk_frequency(keyword):
    try:
        with open("/sys/kernel/debug/clk/clk_summary") as f:
            for line in f:
                if keyword in line:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        return round(int(parts[4]) / 1_000_000, 1)
    except:
        pass
    return None

# === ADVANCED CLOCK FREQUENCIES ===
CRU_SIZE = 0x1000

CRU_BASE            = 0xFD7C0000
CRU_BIGCORE0_BASE   = 0xFD810000
CRU_BIGCORE1_BASE   = 0xFD812000
CRU_DSU_BASE        = 0xFD818000
CRU_DDRPHY0_BASE    = 0xFD800000 # reading locks device
CRU_DDRPHY1_BASE    = 0xFD804000 # reading locks device
CRU_DDRPHY2_BASE    = 0xFD808000 # reading locks device
CRU_DDRPHY3_BASE    = 0xFD80C000 # reading locks device

GRF_BIGCORE0_BASE   = 0xFD590000
GRF_BIGCORE1_BASE   = 0xFD592000
GRF_LITCORE_BASE    = 0xFD594000
GRF_DSU_BASE        = 0xFD598000
GRF_GPU_BASE        = 0xFD5A0000 # reading when gpu_policy != always_on locks device
GRF_NPU_BASE        = 0xFD5A2000 # reading locks device // pvtpll possibly not enabled for NPU
GRF_DDR01_BASE      = 0xFD59C000 # reading locks device
GRF_DDR23_BASE      = 0xFD59D000 # reading locks device

CRU_CLKSEL_CON73 = 0x0424
CRU_CLKSEL_CON74 = 0x0428
CRU_CLKSEL_CON158 = 0x0578
CRU_CLKSEL_CON159 = 0x057C
CRU_CLKSEL_CON160 = 0x0584
CRU_CLKSEL_CON161 = 0x058C

BIGCORE0_B0PLL_CON0 = 0x0000
BIGCORE0_B0PLL_CON1 = 0x0004
BIGCORE0_B0PLL_CON6 = 0x0018
BIGCORE0_MODE_CON00 = 0x0280
BIGCORE0_CLKSEL_CON00 = 0x0300
BIGCORE0_CLKSEL_CON01 = 0x0304

BIGCORE1_B1PLL_CON0 = 0x0020
BIGCORE1_B1PLL_CON1 = 0x0024
BIGCORE1_B1PLL_CON6 = 0x0038
BIGCORE1_MODE_CON00 = 0x0280
BIGCORE1_CLKSEL_CON00 = 0x0300
BIGCORE1_CLKSEL_CON01 = 0x0304

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

GRF_BIGCORE0_PVTPLL = 0x18
GRF_BIGCORE1_PVTPLL = 0x18
GRF_LITCORE_PVTPLL = 0x60
GRF_DSU_PVTPLL = 0x80
GRF_GPU_PVTPLL = 0x18
GRF_NPU_PVTPLL = 0x24

XIN_OSC0_FREQ       = 24        # RK3588 TRM
DEEPSLOW_FREQ       = 0.032     # arbirary value
CLEAN_FREQ          = 100       # arbirary value

AUPLL_FREQ          = 786       # mmm tool
CPLL_FREQ           = 1500      # mmm tool
GPLL_FREQ           = 1188      # mmm tool
NPLL_FREQ           = 850       # mmm tool
SPLL_FREQ           = 702       # rk3588 dts
V0PLL_FREQ          = 1188      # mmm tool

def get_bits(value, lsb, msb):
    mask = (1 << (msb - lsb + 1)) - 1
    return (value >> lsb) & mask

class CRUMemory:
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

def read_field(mem, field):
    name, offset, lsb, msb, ftype, enum_map, value_range = field
    val = mem.read32(offset)
    width = msb - lsb + 1
    mask = (1 << width) - 1
    raw = (val >> lsb) & mask
    
    if ftype == "enum" and enum_map:
        # Return enum key for the raw value or a default string
        for key, enum_val in enum_map.items():
            if enum_val == raw:
                return key
        return f"unknown({raw})"
    return raw

def is_gpu_pvtpll_safe_to_read():
    try:
        with open("/sys/devices/platform/fb000000.gpu/devfreq/fb000000.gpu/device/power_policy", "r") as f:
            return f.read().strip() == "coarse_demand [always_on]"
    except FileNotFoundError:
        return False  # If sysfs path doesn't exist, assume not safe

SECTIONS = [
            (GRF_BIGCORE0_BASE, [
            ("bigcore0_pvtpll_freq", GRF_BIGCORE0_PVTPLL ,(0, 31),"int", None, None), 
        ]),
            (GRF_BIGCORE1_BASE, [
            ("bigcore1_pvtpll_freq", GRF_BIGCORE1_PVTPLL ,(0, 31),"int", None, None), 
        ]),
            (GRF_LITCORE_BASE, [
            ("litcore_pvtpll_freq", GRF_LITCORE_PVTPLL ,(0, 31),"int", None, None), 
        ]),            
            (GRF_DSU_BASE, [
            ("dsu_pvtpll_freq", GRF_DSU_PVTPLL ,(0, 31),"int", None, None), 
        ]),            
            (GRF_GPU_BASE, [
            ("gpu_pvtpll_freq", GRF_GPU_PVTPLL ,(0, 31),"int", None, None), 
        ]),            
            (CRU_BASE, [
            ("gpu_pvtpll_sel", CRU_CLKSEL_CON158, (2, 2), "enum",
             {"clk_gpu_src": 0b0, "xin_osc0_func": 0b1}),
            ("gpu_src_div", CRU_CLKSEL_CON158, (0, 4), "int", None, (0, 31)),
            ("gpu_src_sel", CRU_CLKSEL_CON158, (5, 7), "enum",
             {"gpll": 0b000, "cpll": 0b001, "aupll": 0b010, "npll": 0b011, "spll": 0b100}),            
            ("gpu_src_mux_sel", CRU_CLKSEL_CON158, (14, 14), "enum",
             {"gpu_src": 0b0, "PVTPLL": 0b1}),
            ("rknn_dsu0_src_div", CRU_CLKSEL_CON73, (2, 6), "int", None, (0, 31)),
            ("rknn_dsu0_src_sel", CRU_CLKSEL_CON73, (7, 9), "enum",
             {"gpll": 0b000, "cpll": 0b001, "aupll": 0b010, "npll": 0b011, "spll": 0b100}),
            ("rknn_dsu0_mux_sel", CRU_CLKSEL_CON74, (0, 0), "enum",
             {"dsu0_src": 0b0, "PVTPLL": 0b1}),
            ("npu_pvtpll_sel", CRU_CLKSEL_CON74, (4, 4), "enum",
             {"rknn_dsu0_src": 0b0, "xin_osc0_func": 0b1}),            
            ("npu_cm0_rtc_div", CRU_CLKSEL_CON74, (7, 11), "int", None, (0, 31)),
        ]),       
            (CRU_BIGCORE0_BASE, [
            ("m_b0pll", BIGCORE0_B0PLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_b0pll", BIGCORE0_B0PLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_b0pll", BIGCORE0_B0PLL_CON1, (6, 8), "int", None, (0, 6)),
            ("clk_b0pll_mux", BIGCORE0_MODE_CON00, (0, 1), "enum",
             {"xin_osc0_func": 0b00, "clk_b0pll": 0b01, "clk_deepslow": 0b10}),
            ("b0pll_pll_reset", BIGCORE0_B0PLL_CON1, (13, 13), "int", None, (0, 1)),
            ("b0pll_lock", BIGCORE0_B0PLL_CON6, (15, 15), "int", None, (0, 1)),
            ("bigcore0_slow_sel", BIGCORE0_CLKSEL_CON00, (0, 0), "enum",
             {"xin_osc0_func": 0b0, "clk_deepslow": 0b1}),
            ("bigcore0_gpll_div", BIGCORE0_CLKSEL_CON00, (1, 5), "int", None, (0, 31)),
            ("bigcore0_mux_sel", BIGCORE0_CLKSEL_CON00, (6, 7), "enum",
             {"slow": 0b00, "gpll": 0b01, "b0pll": 0b10}),
            ("b0_uc_div", BIGCORE0_CLKSEL_CON00, (8, 12), "int", None, (0, 31)),
            ("b0_clk_sel", BIGCORE0_CLKSEL_CON00, (13, 14), "enum",
             {"UC_b0": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
            ("b1_uc_div", BIGCORE0_CLKSEL_CON01, (0, 4), "int", None, (0, 31)),
            ("b1_clk_sel", BIGCORE0_CLKSEL_CON01, (5, 6), "enum",
             {"UC_b1": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
        ]),
            (CRU_BIGCORE1_BASE, [
            ("m_b1pll", BIGCORE1_B1PLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_b1pll", BIGCORE1_B1PLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_b1pll", BIGCORE1_B1PLL_CON1, (6, 8), "int", None, (0, 6)),
            ("clk_b1pll_mux", BIGCORE1_MODE_CON00, (0, 1), "enum",
             {"xin_osc0_func": 0b00, "clk_b1pll": 0b01, "clk_deepslow": 0b10}),
            ("b1pll_pll_reset", BIGCORE1_B1PLL_CON1, (13, 13), "int", None, (0, 1)),
            ("b1pll_lock", BIGCORE1_B1PLL_CON6, (15, 15), "int", None, (0, 1)),
            ("bigcore1_slow_sel", BIGCORE1_CLKSEL_CON00, (0, 0), "enum",
             {"xin_osc0_func": 0b0, "clk_deepslow": 0b1}),
            ("bigcore1_gpll_div", BIGCORE1_CLKSEL_CON00, (1, 5), "int", None, (0, 31)),
            ("bigcore1_mux_sel", BIGCORE1_CLKSEL_CON00, (6, 7), "enum",
             {"slow": 0b00, "gpll": 0b01, "b1pll": 0b10}),
            ("b2_uc_div", BIGCORE1_CLKSEL_CON00, (8, 12), "int", None, (0, 31)),
            ("b2_clk_sel", BIGCORE1_CLKSEL_CON00, (13, 14), "enum",
             {"UC_b2": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
            ("b3_uc_div", BIGCORE1_CLKSEL_CON01, (0, 4), "int", None, (0, 31)),
            ("b3_clk_sel", BIGCORE1_CLKSEL_CON01, (5, 6), "enum",
             {"UC_b3": 0b00, "Clean": 0b01, "PVTPLL": 0b10}),
        ]),
            (CRU_DSU_BASE, [
            ("m_lpll", DSU_LPLL_CON0, (0, 9), "int", None, (64, 1023)),
            ("p_lpll", DSU_LPLL_CON1, (0, 5), "int", None, (1, 63)),
            ("s_lpll", DSU_LPLL_CON1, (6, 8), "int", None, (0, 6)),
            ("clk_lpll_mux", DSU_MODE_CON00, (0, 1), "enum",
             {"xin_osc0_func": 0b00, "clk_lpll": 0b01, "clk_deepslow": 0b10}),
            ("lpll_pll_reset", DSU_LPLL_CON1, (13, 13), "int", None, (0, 1)),
            ("lpll_lock", DSU_LPLL_CON6, (15, 15), "int", None, (0, 1)),
            ("littlecore_slow_sel", DSU_CLKSEL_CON00, (0, 0), "enum",
             {"xin_osc0_func": 0b0, "clk_deepslow": 0b1}),
            ("littlecore_gpll_div", DSU_CLKSEL_CON05, (9, 13), "int", None, (0, 31)),
            ("littlecore_mux_sel", DSU_CLKSEL_CON05, (14, 15), "enum",
             {"slow": 0b00, "gpll": 0b01, "lpll": 0b10}),
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
            ("dsu_sclk_df_src_mux_sel", DSU_CLKSEL_CON00, (12, 13), "enum",
             {"b0pll": 0b00, "b1pll": 0b01, "lpll": 0b10, "gpll": 0b11}),
            ("dsu_sclk_df_src_mux_div", DSU_CLKSEL_CON00, (7, 11), "int", None, (0, 31)),
            ("dsu_sclk_src_t_sel", DSU_CLKSEL_CON01, (0, 0), "enum",
             {"dsu_src": 0b0, "PVTPLL": 0b01}),
            ("dsu_pclk_root_mux_sel", DSU_CLKSEL_CON04, (5, 6), "enum",
             {"b0pll": 0b00, "b1pll": 0b01, "lpll": 0b10, "gpll": 0b11}),
            ("dsu_pclk_root_mux_div", DSU_CLKSEL_CON04, (0, 4), "int", None, (0, 31)),
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
for base_addr, fields in SECTIONS:
    for entry in fields:
        name, addr, bit_range, ftype = entry[:4]
        enum_map = entry[4] if len(entry) > 4 else None
        vrange = entry[5] if len(entry) > 5 else None
        lsb, msb = bit_range
        FLAT_FIELDS.append((name, base_addr, addr, lsb, msb, ftype, enum_map, vrange))

class ClockMonitor:
    def __init__(self):
        self.data = {}
        self.mem_map = {}
        for base_addr, _ in SECTIONS:
            try:
                self.mem_map[base_addr] = CRUMemory(base_addr, CRU_SIZE)
            except PermissionError:
                print(f"Permission denied: cannot open /dev/mem at base {base_addr:#x}. Run as root.")
                exit(1)

    def read_reg(self, mem, offset):
        mem.seek(offset)
        data = mem.read(4)
        return struct.unpack('<I', data)[0]

    def read_freq(self, base, avg_cnt_offset):
        with open("/dev/mem", "rb") as f:
            mem = mmap.mmap(f.fileno(), GRF_SIZE, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)
            try:
                return read_reg(mem, avg_cnt_offset)
            finally:
                mem.close()

    def get_val(self, name, flat_fields):
        for f in flat_fields:
            f_name, base_addr, offset, lsb, msb, ftype, enum_map, vrange = f
            if f_name == name:
                mem = self.mem_map.get(base_addr)
                if mem is None:
                    raise ValueError(f"No memory mapped for base address {base_addr:#x}")
                reg_val = mem.read32(offset)
                width = msb - lsb + 1
                mask = (1 << width) - 1
                raw = (reg_val >> lsb) & mask
                if ftype == "enum" and enum_map is not None:
                    return next((k for k, v in enum_map.items() if v == raw), f"unknown({raw})")
                else:
                    return raw
        return None

    def update(self):
        # bigCore0 Calculations
        m_b0 = self.get_val("m_b0pll", FLAT_FIELDS)
        p_b0 = self.get_val("p_b0pll", FLAT_FIELDS)
        s_b0 = self.get_val("s_b0pll", FLAT_FIELDS)
        b0pll_freq = (XIN_OSC0_FREQ * m_b0) / p_b0 / (1 << s_b0) if m_b0 and p_b0 else 0

        bigcore0_slow_sel = self.get_val("bigcore0_slow_sel", FLAT_FIELDS)
        bigcore0_gpll_div = self.get_val("bigcore0_gpll_div", FLAT_FIELDS)
        bigcore0_mux_sel = self.get_val("bigcore0_mux_sel", FLAT_FIELDS)

        b0_uc_div = self.get_val("b0_uc_div", FLAT_FIELDS)
        b1_uc_div = self.get_val("b1_uc_div", FLAT_FIELDS)

        b0_clk_sel = self.get_val("b0_clk_sel", FLAT_FIELDS)
        b1_clk_sel = self.get_val("b1_clk_sel", FLAT_FIELDS)

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

        bigcore0_pvtpll_freq = self.get_val("bigcore0_pvtpll_freq", FLAT_FIELDS)

        def get_clk_freq(sel, uc_clk, pvtpll_freq):
            if sel in ("UC_b0", "UC_b1"):
                return uc_clk
            elif sel == "Clean":
                return CLEAN_FREQ
            elif sel == "PVTPLL":
                return pvtpll_freq
            else:
                return 0

        b0_clk_freq = get_clk_freq(b0_clk_sel, b0_uc_clk, bigcore0_pvtpll_freq)
        b1_clk_freq = get_clk_freq(b1_clk_sel, b1_uc_clk, bigcore0_pvtpll_freq)

        # BigCore1 Calculations
        m_b1 = self.get_val("m_b1pll", FLAT_FIELDS)
        p_b1 = self.get_val("p_b1pll", FLAT_FIELDS)
        s_b1 = self.get_val("s_b1pll", FLAT_FIELDS)
        b1pll_freq = (XIN_OSC0_FREQ * m_b1) / p_b1 / (1 << s_b1) if m_b1 and p_b1 else 0

        bigcore1_slow_sel = self.get_val("bigcore1_slow_sel", FLAT_FIELDS)
        bigcore1_gpll_div = self.get_val("bigcore1_gpll_div", FLAT_FIELDS)
        bigcore1_mux_sel = self.get_val("bigcore1_mux_sel", FLAT_FIELDS)

        b2_uc_div = self.get_val("b2_uc_div", FLAT_FIELDS)
        b3_uc_div = self.get_val("b3_uc_div", FLAT_FIELDS)

        b2_clk_sel = self.get_val("b2_clk_sel", FLAT_FIELDS)
        b3_clk_sel = self.get_val("b3_clk_sel", FLAT_FIELDS)

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

        bigcore1_pvtpll_freq = self.get_val("bigcore1_pvtpll_freq", FLAT_FIELDS)

        def get_clk_freq(sel, uc_clk, pvtpll_freq):
            if sel in ("UC_b2", "UC_b3"):
                return uc_clk
            elif sel == "Clean":
                return CLEAN_FREQ
            elif sel == "PVTPLL":
                return pvtpll_freq
            else:
                return 0

        b2_clk_freq = get_clk_freq(b2_clk_sel, b2_uc_clk, bigcore1_pvtpll_freq)
        b3_clk_freq = get_clk_freq(b3_clk_sel, b3_uc_clk, bigcore1_pvtpll_freq)

        # LittleCore Calculations
        m_l = self.get_val("m_lpll", FLAT_FIELDS)
        p_l = self.get_val("p_lpll", FLAT_FIELDS)
        s_l = self.get_val("s_lpll", FLAT_FIELDS)

        lpll_freq = (XIN_OSC0_FREQ * m_l) / p_l / (1 << s_l) if m_l and p_l else 0

        littlecore_slow_sel = self.get_val("littlecore_slow_sel", FLAT_FIELDS)
        littlecore_gpll_div = self.get_val("littlecore_gpll_div", FLAT_FIELDS)
        littlecore_mux_sel = self.get_val("littlecore_mux_sel", FLAT_FIELDS)

        l0_uc_div = self.get_val("l0_uc_div", FLAT_FIELDS)
        l1_uc_div = self.get_val("l1_uc_div", FLAT_FIELDS)
        l2_uc_div = self.get_val("l2_uc_div", FLAT_FIELDS)
        l3_uc_div = self.get_val("l3_uc_div", FLAT_FIELDS)

        l0_clk_sel = self.get_val("l0_clk_sel", FLAT_FIELDS)
        l1_clk_sel = self.get_val("l1_clk_sel", FLAT_FIELDS)
        l2_clk_sel = self.get_val("l2_clk_sel", FLAT_FIELDS)
        l3_clk_sel = self.get_val("l3_clk_sel", FLAT_FIELDS)

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

        litcore_pvtpll_freq = self.get_val("litcore_pvtpll_freq", FLAT_FIELDS)

        def get_clk_freq(sel, uc_clk, pvtpll_freq):
            if sel in ("UC_l0", "UC_l1", "UC_l2", "UC_l3"):
                return uc_clk
            elif sel == "Clean":
                return CLEAN_FREQ
            elif sel == "PVTPLL":
                return pvtpll_freq
            else:
                return 0

        l0_clk_freq = get_clk_freq(l0_clk_sel, l0_uc_clk, litcore_pvtpll_freq)
        l1_clk_freq = get_clk_freq(l1_clk_sel, l1_uc_clk, litcore_pvtpll_freq)
        l2_clk_freq = get_clk_freq(l2_clk_sel, l2_uc_clk, litcore_pvtpll_freq)
        l3_clk_freq = get_clk_freq(l3_clk_sel, l3_uc_clk,litcore_pvtpll_freq)

        # DSU Calculations
        dsu_sclk_df_src_mux_sel = self.get_val("dsu_sclk_df_src_mux_sel", FLAT_FIELDS)
        dsu_sclk_df_src_mux_div = self.get_val("dsu_sclk_df_src_mux_div", FLAT_FIELDS)
        dsu_sclk_src_t_sel = self.get_val("dsu_sclk_src_t_sel", FLAT_FIELDS) 

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

        dsu_pvtpll_freq = self.get_val("dsu_pvtpll_freq", FLAT_FIELDS)

        if dsu_sclk_src_t_sel == "dsu_src":
            sclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_sclk_df_src_mux_div + 1)
        elif dsu_sclk_src_t_sel == "PVTPLL":
            sclk_clk_freq = dsu_pvtpll_freq
        else:
            sclk_clk_freq = 0

        dsu_pclk_root_mux_sel = self.get_val("dsu_pclk_root_mux_sel", FLAT_FIELDS)
        dsu_pclk_root_mux_div = self.get_val("dsu_pclk_root_mux_div", FLAT_FIELDS)

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

        dsu_aclkm_div = self.get_val("dsu_aclkm_div", FLAT_FIELDS)
        dsu_aclks_div = self.get_val("dsu_aclks_div", FLAT_FIELDS)
        dsu_aclkmp_div = self.get_val("dsu_aclkmp_div", FLAT_FIELDS)
        dsu_periphclk_div = self.get_val("dsu_periphclk_div", FLAT_FIELDS)
        dsu_cntclk_div = self.get_val("dsu_cntclk_div", FLAT_FIELDS)
        dsu_tsclk_div = self.get_val("dsu_tsclk_div", FLAT_FIELDS)
        dsu_atclk_div = self.get_val("dsu_atclk_div", FLAT_FIELDS)
        dsu_gicclk_div = self.get_val("dsu_gicclk_t_div", FLAT_FIELDS)

        aclkm_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_aclkm_div + 1)
        aclks_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_aclks_div + 1)
        aclkmp_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_aclkmp_div + 1)
        periphclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_periphclk_div + 1)
        cntclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_cntclk_div + 1)
        tsclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_tsclk_div + 1)
        atclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_atclk_div + 1)
        gicclk_clk_freq = dsu_sclk_df_src_mux_clk / (dsu_gicclk_div + 1)

        # GPU Calculations
        gpu_src_sel = self.get_val("gpu_src_sel", FLAT_FIELDS)
        gpu_src_div = self.get_val("gpu_src_div", FLAT_FIELDS)
        gpu_src_mux_sel = self.get_val("gpu_src_mux_sel", FLAT_FIELDS)

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

        if is_gpu_pvtpll_safe_to_read():
            gpu_pvtpll_freq = self.get_val("gpu_pvtpll_freq", FLAT_FIELDS)
        else:
            gpu_pvtpll_freq = -1

        if gpu_src_mux_sel == "gpu_src":
            gpu_clk_freq = gpu_src_mux_clk
        elif gpu_src_mux_sel == "PVTPLL":
            gpu_clk_freq = gpu_pvtpll_freq
        else:
            gpu_clk_freq = 0

        # NPU Calculations
        dsu0_src_sel = self.get_val("rknn_dsu0_src_sel", FLAT_FIELDS)
        dsu0_src_div = self.get_val("rknn_dsu0_src_div", FLAT_FIELDS)
        dsu0_mux_sel = self.get_val("rknn_dsu0_mux_sel", FLAT_FIELDS)

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

        if dsu0_mux_sel == "dsu0_src":
            npu_clk_freq = dsu0_src_mux_clk
        elif dsu0_mux_sel == "PVTPLL":
            npu_clk_freq = -1 # replace with npu_clk_freq = npu_pvtpll_freq when pvtpll can be read
        else:
            npu_clk_freq = 0

        self.data = {
            "l0":           (l0_clk_freq, l0_clk_sel),
            "l1":           (l1_clk_freq, l1_clk_sel),
            "l2":           (l2_clk_freq, l2_clk_sel),
            "l3":           (l3_clk_freq, l3_clk_sel),
            "b0":           (b0_clk_freq, b0_clk_sel),
            "b1":           (b1_clk_freq, b1_clk_sel),
            "b2":           (b2_clk_freq, b2_clk_sel),
            "b3":           (b3_clk_freq, b3_clk_sel),
            "gpu":          (gpu_clk_freq, gpu_src_mux_sel),
            "npu":          (npu_clk_freq, dsu0_mux_sel),
            "dsu_sclk":     (sclk_clk_freq, dsu_sclk_src_t_sel),
            "aclkm":        (aclkm_clk_freq, None),
            "aclks":        (aclks_clk_freq, None),
            "aclkmp":       (aclkmp_clk_freq, None),
            "periphclk":    (periphclk_clk_freq, None),
            "cntclk":       (cntclk_clk_freq, None),
            "tsclk":        (tsclk_clk_freq, None),
            "atclk":        (atclk_clk_freq, None),
            "gicclk":       (gicclk_clk_freq, None),
            "pclk":         (pclk_clk_freq, None),
        }

    def get(self, key):
        return self.data.get(key, (0, None))

    def all(self):
        return self.data.items()

monitor = ClockMonitor()

# === VOLTAGES ===
def get_sorted_regulator_voltages():
    voltages = []
    for reg in glob.glob("/sys/class/regulator/regulator.*"):
        name = read_file(os.path.join(reg, "name"))
        uV = read_file(os.path.join(reg, "microvolts"))
        if name and uV:
            try:
                voltages.append((name, round(int(uV) / 1000)))
            except:
                continue
    return sorted(voltages)

# === TEMPERATURES ===
def get_temperatures():
    temperatures = []
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        name = read_file(os.path.join(hwmon, "name"))
        temp = read_file(os.path.join(hwmon, "temp1_input")) 
        if name and temp:
            try:
                temperatures.append((name, round(int(temp) / 1000, 1)))
            except:
                continue
    return sorted(temperatures, key=lambda x: x[0])


# === LOAD & USAGE ===
def read_cpu_times():
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("cpu"):
                parts = line.split()
                cpu_id = parts[0]
                times = list(map(int, parts[1:]))
                yield cpu_id, times

_prev_cpu_times = None

def get_cpu_usages(interval=1.0):
    global _prev_cpu_times

    curr = dict(read_cpu_times())

    if _prev_cpu_times is None:
        _prev_cpu_times = curr
        time.sleep(interval)
        curr = dict(read_cpu_times())

    usage = {}
    for cpu in curr:
        if cpu not in _prev_cpu_times:
            continue
        prev = _prev_cpu_times[cpu]
        curr_times = curr[cpu]

        prev_total = sum(prev)
        curr_total = sum(curr_times)
        prev_idle = prev[3] + prev[4]  # idle + iowait
        curr_idle = curr_times[3] + curr_times[4]

        total_delta = curr_total - prev_total
        idle_delta = curr_idle - prev_idle

        if total_delta == 0:
            usage[cpu.upper()] = 0.0
        else:
            usage[cpu.upper()] = round(100 * (1 - idle_delta / total_delta), 1)

    _prev_cpu_times = curr
    return usage

def get_load_values():
    loads = {}
    paths = {
        "DMC": "/sys/devices/platform/dmc/devfreq/dmc/load",
        "GPU": "/sys/devices/platform/fb000000.gpu/devfreq/fb000000.gpu/load",
        "NPU": "/sys/devices/platform/fdab0000.npu/devfreq/fdab0000.npu/load",
    }
    for label, path in paths.items():
        val = read_file(path)
        if val is not None:
            val = val.strip()
            try:
                if "@" in val:
                    val = val.split("@")[0]
                loads[label] = int(val)
            except ValueError:
                continue
    return loads

# === GOVERNORS ===
def get_governors():
    governors = {}
    paths = {
        "CPU0":     "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
        "CPU1":     "/sys/devices/system/cpu/cpu1/cpufreq/scaling_governor",
        "CPU2":     "/sys/devices/system/cpu/cpu2/cpufreq/scaling_governor",
        "CPU3":     "/sys/devices/system/cpu/cpu3/cpufreq/scaling_governor",
        "CPU4":     "/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor",
        "CPU5":     "/sys/devices/system/cpu/cpu5/cpufreq/scaling_governor",
        "CPU6":     "/sys/devices/system/cpu/cpu6/cpufreq/scaling_governor",
        "CPU7":     "/sys/devices/system/cpu/cpu7/cpufreq/scaling_governor",
        "DMC":      "/sys/devices/platform/dmc/devfreq/dmc/governor",
        "GPU":      "/sys/devices/platform/fb000000.gpu/devfreq/fb000000.gpu/governor",
        "GPU_VR":   "/sys/devices/platform/fb000000.gpu/devfreq/fb000000.gpu/device/power_policy",
        "NPU":      "/sys/devices/platform/fdab0000.npu/devfreq/fdab0000.npu/governor",
        "PCIE":     "/sys/module/pcie_aspm/parameters/policy",
    }

    for label, path in paths.items():
        val = read_file(path)
        if val:
            match = re.search(r"\[([^\]]+)\]", val)
            if match:
                governors[label] = match.group(1)
            else:
                governors[label] = val    
    return governors

# === SAR-DAC
def get_sar_adc_readings():
    base_path = "/sys/devices/iio_sysfs_trigger/subsystem/devices/iio:device0/"
    adc_data = []

    try:
        scale_path = os.path.join(base_path, "in_voltage_scale")
        scale_str = read_file(scale_path)
        scale = float(scale_str) if scale_str else 1.0
    except:
        scale = 1.0

    for i in range(8):  # 0 to 7
        raw_path = os.path.join(base_path, f"in_voltage{i}_raw")
        raw_str = read_file(raw_path)
        if raw_str:
            try:
                raw_val = int(raw_str)
                scaled_val = raw_val * scale
                adc_data.append((f"SARADC{i}", raw_val, scaled_val))
            except:
                continue
    return adc_data

# === TUI FUNCTIONS ===
def tui_main(stdscr, args):
    import curses
    import signal

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)

    stdscr.clear()
    stdscr.nodelay(True)
    curses.curs_set(0)

    def handle_resize(signum, frame):
        curses.resizeterm(*stdscr.getmaxyx())
        stdscr.clear()
    signal.signal(signal.SIGWINCH, handle_resize)

    COLUMN_MAP = {
        't': 1,
        'f': 0,
        'af': 0,
        'p': 0,
        'g': 1,
        'v': 2,
        'l': 1,
        's': 1,
    }

    try:
        while True:
            monitor.update()
            stdscr.erase()
            max_y, max_x = stdscr.getmaxyx()
            col_width = max_x // 3 - 1
            start_row = 5
            cols = [[], [], []]

            # Header
            stdscr.addstr(0, 0, "RK3588 Telemetry (TUI) for Orange Pi 5 Max by SkatterBencher (v0.12) - Press 'q' to quit", curses.color_pair(1) | curses.A_BOLD)
            interval_text = f"Update interval: {args.i:.1f} seconds"
            log_status = "Logging: ENABLED" if args.log else "Logging: DISABLED"
            stdscr.addstr(2, 0, interval_text, curses.A_DIM)
            stdscr.addstr(3, 0, log_status, curses.A_DIM)

            # Add lines to the right column buffer instead of drawing immediately
            def add_lines(lines, col_index):
                for line in lines:
                    if isinstance(line, tuple):
                        text, attr = line
                    else:
                        text, attr = line, 0
                    cols[col_index].append((text, attr))

            # Temperatures
            if args.t:
                lines = [("## Temperatures ##", curses.color_pair(1) | curses.A_BOLD)]
                for name, temp in get_temperatures():
                    lines.append(f"{name:<20} {temp:.1f} °C")
                lines.append("")
                add_lines(lines, COLUMN_MAP['t'])

            # Frequencies
            if args.f:
                lines = [("## Frequencies ##", curses.color_pair(1) | curses.A_BOLD)]
                for key, label in CLK_ORDERED.items():
                    freq = get_clk_frequency(key)
                    if freq is not None:
                        lines.append(f"{label:<20} {freq:.0f} MHz")
                lines.append("")
                add_lines(lines, COLUMN_MAP['f'])

            # Advanced Frequencies
            if args.af:
                lines = [("## Advanced Frequencies ##", curses.color_pair(1) | curses.A_BOLD)]

                key_map = {
                    "A55_L0": "l0",
                    "A55_L1": "l1",
                    "A55_L2": "l2",
                    "A55_L3": "l3",
                    "A76_B0": "b0",
                    "A76_B1": "b1",
                    "A76_B2": "b2",
                    "A76_B3": "b3",
                    "GPU": "gpu",
                    "NPU": "npu",
                }

                for display_key in ["A55_L0", "A55_L1", "A55_L2", "A55_L3", "A76_B0", "A76_B1", "A76_B2", "A76_B3", "GPU", "NPU"]:
                    data_key = key_map.get(display_key)
                    if data_key is None:
                        freq, sel = 0, None
                    else:
                        freq, sel = monitor.get(data_key)
                    lines.append(f"{display_key + ':':<15} {freq:.0f} MHz ({sel})")

                dsu_labels = {
                    "dsu_sclk": "DSU SCLK:",
                    "aclkm": "DSU ACLK_M:",
                    "aclks": "DSU ACLK_S:",
                    "aclkmp": "DSU ACLK_MP:",
                    "periphclk": "DSU PERIPHCLK:",
                    "cntclk": "DSU CNTCLK:",
                    "tsclk": "DSU TSCLK:",
                    "atclk": "DSU ATCLK:",
                    "gicclk": "DSU GICCLK:",
                    "pclk": "DSU PCLK:",
                }

                for key, label in dsu_labels.items():
                    freq, sel = monitor.get(key)
                    if sel is not None:
                        lines.append(f"{label:<15} {freq:.0f} MHz ({sel})")
                    else:
                        lines.append(f"{label:<15} {freq:.0f} MHz")

                lines.append("")
                add_lines(lines, COLUMN_MAP['af'])

            # Governors
            if args.g:
                lines = [("## Governors ##", curses.color_pair(1) | curses.A_BOLD)]
                for label, val in get_governors().items():
                    lines.append(f"{label:<20} {val}")
                lines.append("")
                add_lines(lines, COLUMN_MAP['g'])

            # Voltages
            if args.v:
                lines = [("## Voltages ##", curses.color_pair(1) | curses.A_BOLD)]
                for name, uV in get_sorted_regulator_voltages():
                    lines.append(f"{name:<20} {uV} mV")
                lines.append("")
                add_lines(lines, COLUMN_MAP['v'])

            # Loads
            if args.l:
                lines = [("## Loads ##", curses.color_pair(1) | curses.A_BOLD)]
                usages = get_cpu_usages()
                for label, usage in usages.items():
                    lines.append(f"{label:<20} {usage} %")
                loads = get_load_values()
                for label, val in loads.items():
                    lines.append(f"{label:<20} {val} %")
                lines.append("")
                add_lines(lines, COLUMN_MAP['l'])

            # SAR-ADC
            if getattr(args, "s", False):
                lines = [("## SAR-ADC (Scaled) ##", curses.color_pair(1) | curses.A_BOLD)]
                for name, raw, scaled in get_sar_adc_readings():
                    lines.append(f"{name:<20} {scaled:.0f}")
                lines.append("")
                add_lines(lines, COLUMN_MAP['s'])

            # Draw all columns
            for col_idx, col_lines in enumerate(cols):
                x = col_idx * (col_width + 1)
                for row_idx, (text, attr) in enumerate(col_lines):
                    y = start_row + row_idx
                    if y < max_y:
                        stdscr.addstr(y, x, text[:col_width], attr)

            stdscr.refresh()

            # Exit
            ch = stdscr.getch()
            if ch == ord('q'):
                break

            # Refresh delay
            curses.napms(max(100, int(args.i * 1000)))

    except KeyboardInterrupt:
        curses.endwin()
        print("\n#########################")
        print("\nTelemetry stopped by user")
        print("\n#########################")

def main(args=None):
    if args is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("-f", action="store_true", help="Show frequencies")
        parser.add_argument("-af", action="store_true", help="Show advanced frequencies")
        parser.add_argument("-v", action="store_true", help="Show voltages") 
        parser.add_argument("-l", action="store_true", help="Show loads")
        parser.add_argument("-t", action="store_true", help="Show temperatures")
        parser.add_argument("-g", action="store_true", help="Show governors")
        parser.add_argument("-s", action="store_true", help="Show sar-dac")
        parser.add_argument("-log", action="store_true", help="Log to CSV")
        parser.add_argument("-i", type=float, default=2.0, help="Refresh interval in seconds")
        parser.add_argument("-tui", action="store_true", help="Run with TUI interface")
        args = parser.parse_args()

    if not any([args.f, args.af, args.v, args.l, args.t, args.g,]):
        args.f = args.af = args.v = args.l = args.t = args.g = args.s = args.tui = True

    # Enable all metrics if TUI is used
    if args.tui:
        args.f = args.af = args.v = args.l = args.t = args.g = args.s = True

    stop_event = threading.Event()
    log_thread = None

    if args.log:
        monitor = ClockMonitor()

        def logger():
            now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            log_filename = f"telemetry-{now}.csv"
            with open(log_filename, mode="w", newline="") as log_file:
                csv_writer = csv.writer(log_file)
                wrote_header = False
                while not stop_event.is_set():
                    row = [datetime.datetime.now().isoformat()]
                    headers = ["Timestamp"]

                    if args.f:
                        for key, label in CLK_ORDERED.items():
                            freq = get_clk_frequency(key)
                            if freq is not None:
                                row.append(freq)
                                headers.append(label + " (MHz)")

                    if args.af:
                        monitor.update()
                        af_keys = [
                            ("l0", "A55_L0"),
                            ("l1", "A55_L1"),
                            ("l2", "A55_L2"),
                            ("l3", "A55_L3"),
                            ("b0", "A76_B0"),
                            ("b1", "A76_B1"),
                            ("b2", "A76_B2"),
                            ("b3", "A76_B3"),
                            ("gpu", "GPU"),
                            ("npu", "NPU"),                           
                            ("dsu_sclk", "DSU SCLK"),
                            ("aclkm", "ACLK_M"),
                            ("aclks", "ACLK_S"),
                            ("aclkmp", "ACLK_MP"),
                            ("periphclk", "PERIPHCLK"),
                            ("cntclk", "CNTCLK"),
                            ("tsclk", "TSCLK"),
                            ("atclk", "ATCLK"),
                            ("gicclk", "GICCLK"),
                            ("pclk", "PCLK"),
                        ]

                        for key, label in af_keys:
                            freq, sel = monitor.get(key)
                            row.append(freq)
                            headers.append(f"{label} (MHz)")
                            if sel is not None:
                                row.append(sel)
                                headers.append(f"{label} Source")

                    if args.v:
                        for name, uV in get_sorted_regulator_voltages():
                            row.append(uV)
                            headers.append(name + " (mV)")

                    if args.t:
                        for name, temp in get_temperatures():
                            row.append(temp)
                            headers.append(name + " (°C)")

                    if args.g:
                        governors = get_governors()
                        for label, val in governors.items():
                            row.append(val)
                            headers.append(label + " Governor")

                    if args.l:
                        usages = get_cpu_usages()
                        for label, usage in usages.items():
                            row.append(usage)
                            headers.append(label + " Usage (%)")

                        loads = get_load_values()
                        for label, val in loads.items():
                            row.append(val)
                            headers.append(label + " Load (%)")

                    if args.s:
                        for name, raw, scaled in get_sar_adc_readings():
                            row.extend([raw, scaled])
                            headers.extend([f"{name} Raw", f"{name} Scaled"])

                    if not wrote_header:
                        csv_writer.writerow(headers)
                        wrote_header = True
                    csv_writer.writerow(row)

                    time.sleep(args.i)

        log_thread = threading.Thread(target=logger, daemon=True)
        log_thread.start()

    if args.tui:
        try:
            curses.wrapper(tui_main, args)
        finally:
            if log_thread:
                stop_event.set()
                log_thread.join()
        return

    # Non-TUI CLI output
    try:
        while True:
            os.system("clear")
            row = [datetime.datetime.now().isoformat()]
            headers = ["Timestamp"]

            print("\nRK3588 Telemetry for Orange Pi 5 Max by SkatterBencher, v0.7")

            if args.f:
                show_header("Frequencies")
                for key, label in CLK_ORDERED.items():
                    freq = get_clk_frequency(key)
                    if freq is not None:
                        print(f"{label:<20} {freq:.0f} MHz")
                        row.append(freq)
                        headers.append(label + " (MHz)")

            if args.v:
                show_header("Voltages")
                for name, uV in get_sorted_regulator_voltages():
                    print(f"{name:<20} {uV:.0f} mV")
                    row.append(uV)
                    headers.append(name + " (mV)")

            if args.t:
                show_header("Temperatures")
                for name, temp in get_temperatures():
                    print(f"{name:<20} {temp:.1f} °C")
                    row.append(temp)
                    headers.append(name + " (°C)")

            if args.g:
                show_header("Performance Governors")
                governors = get_governors()
                for label, val in governors.items():
                    print(f"{label:<20} {val}")
                    row.append(val)
                    headers.append(label + " Governor")

            if args.l:
                show_header("Load")
                usages = get_cpu_usages()
                for label, usage in usages.items():
                    print(f"{label:<20} {usage} %")
                    row.append(usage)
                    headers.append(label + " Usage (%)")

                loads = get_load_values()
                for label, val in loads.items():
                    print(f"{label:<20} {val} %")
                    row.append(val)
                    headers.append(label + " Load (%)")

            if args.s:
                show_header("SAR-ADC Readings")
                for name, raw, scaled in get_sar_adc_readings():
                    print(f"{name:<20} {scaled:.0f}")

            if args.log:
                # Logger thread already handles CSV writing
                pass

            time.sleep(args.i)

    except KeyboardInterrupt:
        print("\n#########################")
        print("\nTelemetry stopped by user")
        print("\n#########################")

    finally:
        stop_event.set()
        if log_thread:
            log_thread.join()

if __name__ == "__main__":
    main()
