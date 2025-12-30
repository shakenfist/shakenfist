#!/bin/bash

# Run this from directory containing the generated gRPC code
# Requires: pip install grpcio-tools mypy-protobuf

# First, regenerate the enum definitions from Python source
echo "Generating protobuf enums from Python schemas..."
python3 ../../protos/_generate_enums.py > ../../protos/shakenfist_enums.proto

# Generate Python code, type stubs (.pyi), and gRPC stubs
# --mypy_out generates proper type stubs via mypy-protobuf plugin
# --mypy_grpc_out generates typed stubs for gRPC service definitions
python3 -m grpc_tools.protoc -I../../protos \
    --python_out=. \
    --grpc_python_out=. \
    --mypy_out=. \
    --mypy_grpc_out=. \
    $(find ../../protos -name '*.proto')

# This is terrible, but gRPC lacks a python_package option, so we have to
# tweak the imports in the _grpc.py files.

# Detect OS for sed in-place syntax (macOS uses -i '', Linux uses -i)
if [[ "$OSTYPE" == "darwin"* ]]; then
    SED_INPLACE="sed -i ''"
else
    SED_INPLACE="sed -i"
fi

# Fix imports in .py files - common_pb2 and shakenfist_enums_pb2 are imported
for item in *.py; do
    for importname in common_pb2 shakenfist_enums_pb2; do
        echo "Correcting ${importname} import in ${item}..."
        $SED_INPLACE "s/import ${importname}/from shakenfist.protos import ${importname}/g" ${item}
    done
done

# Fix imports in *_grpc.py files - they also import their corresponding *_pb2
for item in *_grpc.py; do
    importname=$(echo ${item} | sed 's/_grpc.py//')
    echo "Correcting ${importname} import in ${item}..."
    $SED_INPLACE "s/import ${importname}/from shakenfist.protos import ${importname}/g" ${item}
done

# Fix imports in .pyi stub files for mypy
for item in *.pyi; do
    for importname in common_pb2 shakenfist_enums_pb2; do
        echo "Correcting ${importname} import in ${item}..."
        $SED_INPLACE "s/import ${importname}/from shakenfist.protos import ${importname}/g" ${item}
    done
done

for item in *_grpc.pyi; do
    # Extract base name (e.g., "database" from "database_pb2_grpc.pyi")
    importname=$(echo ${item} | sed 's/_pb2_grpc.pyi//')
    echo "Correcting ${importname}_pb2 import in ${item}..."
    $SED_INPLACE "s/^import ${importname}_pb2$/from shakenfist.protos import ${importname}_pb2/g" ${item}
done

# The mypy-protobuf-generated _ServicerContext class causes issues with
# grpc-stubs due to conflicting definitions between grpc.ServicerContext and
# grpc.aio.ServicerContext. The type: ignore comment is kept as-is since we
# need to ignore both [misc] (for the conflicting definitions) and [type-arg]
# (for the missing type parameters).
# No changes needed - keep the original # type: ignore[misc, type-arg] comment.