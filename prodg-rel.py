# IDAPython loader plugin for SN ProDG relocatable DLL files (*.REL)
# These files begin with a SNR2 (SN Relocatable?) header, followed by lists of exported symbol names/addresses
# (likely contains info about relocation too, and imports from the main module & other DLLs, but those aren't implemented here yet)

# Sadly the exported symbols only cover a very small amount of the code - likely only the symbols that main & other modules might access
# (the main module of the game also contains a SNR2 header with exported symbols, but again only covers certain parts of the code, too bad)
# Tested with IDA 7.6 & REL files extracted from PS2 version of Biohazard 4 (JP) bio4dat.afs

import io
import idc 
import idaapi
import ida_segment
import ida_bytes
import ida_loader
import ida_typeinf
import ida_ida
import struct
import ctypes
import os

_MAGIC_SNR2 = "SNR2"
_FORMAT_SNR2 = "SN ProDG relocatable DLL"

char_t = ctypes.c_char
uint8_t  = ctypes.c_byte
uint16_t = ctypes.c_ushort
uint32_t = ctypes.c_uint

# Debug helpers to let us print(structure)
def StructAsString(self):
  return "{}: {{{}}}".format(self.__class__.__name__,
                             ", ".join(["{}: {}".format(field[0],
                                                        getattr(self,
                                                                field[0]))
                                        for field in self._fields_]))

ctypes.BigEndianStructure.__str__ = StructAsString

class MyStructure(ctypes.Structure):
  pass

MyStructure.__str__ = StructAsString

# PE structs & enums
class SNR2Header(MyStructure):
  _fields_ = [
    ("Magic", uint32_t),
    ("RelocTableAddress", uint32_t),
    ("RelocTableCount", uint32_t),
    ("FuncTableAddress", uint32_t),
    ("FuncTableCount", uint32_t),
    ("OriginalImageNameAddress", uint32_t),
    ("GlobalCtorsAddress", uint32_t),
    ("GlobalDtorsAddress", uint32_t),
    ("ExportsAddress", uint32_t),
    ("ExportsCount", uint32_t),
    ("Unk28", uint32_t),
    ("FileSize", uint32_t),
    ("Unk30", uint32_t),
    ("UnkAddr34", uint32_t),
    ("UnkAddr38", uint32_t)
  ]

class SNR2Relocation(MyStructure):
  _pack_ = 1
  _fields_ = [
    ("CodeAddress", uint32_t),
    ("RelocType", uint8_t),
    ("FunctionIdx", uint16_t),
    ("Unk7", uint16_t),
    ("Unk9", uint8_t),
    ("UnkA", uint8_t),
    ("UnkB", uint8_t),
  ]

class SNR2Function(MyStructure):
  _fields_ = [
    ("NameAddress", uint32_t),
    ("CodeAddress", uint32_t),
    ("Unk8", uint16_t),
    ("Type", uint8_t),
    ("UnkB", uint8_t),
  ]

def read_struct(li, struct):
  s = struct()
  slen = ctypes.sizeof(s)
  bytes = li.read(slen)
  fit = min(len(bytes), slen)
  ctypes.memmove(ctypes.addressof(s), bytes, fit)
  return s

def accept_file(li, n):
  li.seek(0)
  magic = li.read(4)
  if magic == bytes(_MAGIC_SNR2, 'utf-8'):
    return _FORMAT_SNR2

  return 0

def load_file(li, neflags, format):

  if format != _FORMAT_SNR2:
    Warning("Unknown format name: '%s'" % format)
    return 0

  idaapi.set_processor_type("r5900l", idc.SETPROC_LOADER)
  ida_typeinf.set_compiler_id(idc.COMP_GNU)
  
  im = ida_ida.compiler_info_t()
  im.id = ida_typeinf.COMP_GNU
  im.cm = 0x03 | 0x00 | 0x30
  im.defalign = 0
  im.size_i = 4
  im.size_b = 1
  im.size_e = 4
  im.size_s = 2
  im.size_l = 4
  im.size_ll = 8
  im.size_ldbl = 8
  
  # Resetting new settings :)
  ida_typeinf.set_compiler(im, ida_typeinf.SETCOMP_OVERRIDE)

  print("[+] SN ProDG relocatable DLL loader by emoose")
  
  li.seek(0)
  snr_header = read_struct(li, SNR2Header)
  
  # header doesn't actually specify where code/data starts & ends, so we need to try working it our ourselves...
  sndata_ext_addr = snr_header.OriginalImageNameAddress
  if sndata_ext_addr == 0 or sndata_ext_addr > snr_header.RelocTableAddress:
    sndata_ext_addr = snr_header.RelocTableAddress
  if sndata_ext_addr == 0 or sndata_ext_addr > snr_header.FuncTableAddress:
    sndata_ext_addr = snr_header.FuncTableAddress
  
  li.seek(snr_header.FuncTableAddress)
  
  funcs = []
  for i in range(0, snr_header.FuncTableCount):
    entry = read_struct(li, SNR2Function)
    funcs.append(entry)
    if sndata_ext_addr == 0 or sndata_ext_addr > entry.NameAddress:
      sndata_ext_addr = entry.NameAddress

  li.seek(snr_header.RelocTableAddress)
  relocs = []
  for i in range(0, snr_header.RelocTableCount):
    entry = read_struct(li, SNR2Relocation)
    relocs.append(entry)

  li.seek(0)
  li.file2base(0, 0, li.size(), 1)
  idaapi.add_segm(0, 0, 0x100, ".sndata", "DATA")
  idaapi.add_segm(0, 0x100, sndata_ext_addr, ".text", "CODE")
  idaapi.add_segm(0, sndata_ext_addr, li.size(), ".sndata2", "DATA")
   # some reason IDA tends to turn first few bytes of sndata_ext_addr to code, why?

  print("found " + str(len(funcs)))
  names = []
  for ent in funcs:
    li.seek(ent.NameAddress)
    name = li.getz(256)
    names.append(name)

    if ent.CodeAddress == 0:
      continue

    idc.set_name(ent.CodeAddress, name)

    if "$" not in name and "__CTOR_LIST__" not in name and "__DTOR_LIST__" not in name:
      #print(hex(ent.CodeAddress) + " = " + name + " (" + hex(ent.Unk8) + " - " + hex(ent.Type) + " - " + hex(ent.UnkB) + ")")
      idc.add_func(ent.CodeAddress)

  # Add comment next to any relocations with the dest function name
  for reloc in relocs:
    reloc_dest_name = names[reloc.FunctionIdx]
    idc.set_cmt(reloc.CodeAddress, reloc_dest_name, 1)

  # Done :)
  print("[+] REL loaded, voila!")
  return 1
