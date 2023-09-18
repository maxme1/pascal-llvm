import ctypes
import sys
from pathlib import Path

import llvmlite.binding as llvm

from pascal_llvm.compiler import Compiler
from pascal_llvm.parser import parse
from pascal_llvm.tokenizer import tokenize

# read
source = Path(sys.argv[1]).read_text()
# compile
tokens = tokenize(source)
program = parse(tokens)
compiler = Compiler()
compiler.visit(program)
# translate
module = llvm.parse_assembly(str(compiler.module))
module.verify()
# run
llvm.initialize()
llvm.initialize_native_target()
llvm.initialize_native_asmprinter()

target = llvm.Target.from_default_triple()
machine = target.create_target_machine()
backing_mod = llvm.parse_assembly("")
engine = llvm.create_mcjit_compiler(backing_mod, machine)

engine.add_module(module)
engine.finalize_object()
engine.run_static_constructors()

main = ctypes.CFUNCTYPE(None)(engine.get_function_address('.main'))
main()
