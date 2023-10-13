import ctypes
import sys
from pathlib import Path

import llvmlite.binding as llvm

from pascal_llvm.compiler import Compiler
from pascal_llvm.parser import parse
from pascal_llvm.tokenizer import tokenize
from pascal_llvm.type_system.visitor import TypeSystem

# read
source = Path(sys.argv[1]).read_text()
# compile
tokens = tokenize(source)
program = parse(tokens)
ts = TypeSystem()
ts.visit(program)
compiler = Compiler(ts)
compiler.visit(program)
module = compiler.module
# translate
module = llvm.parse_assembly(str(module))
module.verify()
# optimize
pm_builder = llvm.PassManagerBuilder()
pm = llvm.ModulePassManager()
pm_builder.populate(pm)
# add passes
pm.add_constant_merge_pass()
pm.add_instruction_combining_pass()
pm.add_reassociate_expressions_pass()
pm.add_gvn_pass()
pm.add_cfg_simplification_pass()
pm.add_loop_simplification_pass()
pm.run(module)
# run
llvm.initialize()
llvm.initialize_native_target()
llvm.initialize_native_asmprinter()

target = llvm.Target.from_default_triple()
machine = target.create_target_machine()
engine = llvm.create_mcjit_compiler(llvm.parse_assembly(""), machine)

engine.add_module(module)
engine.finalize_object()
engine.run_static_constructors()

main = ctypes.CFUNCTYPE(None)(engine.get_function_address('.main'))
main()
