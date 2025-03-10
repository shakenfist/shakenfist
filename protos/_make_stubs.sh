#!/bin/bash

# Run this from directory containing the generated gRPC code

python -m grpc_tools.protoc -I../../protos --python_out=. --pyi_out=. \
    --grpc_python_out=. $(find ../../protos -name *.proto)

# This is terrible, but gRPC lacks a python_package option, so we have to
# tweak the imports in the _grpc.py files.
for item in *.py; do
    importname="common_pb2"
    echo "Correcting ${importname} import in ${item}..."
    sed -i '' "s/import ${importname}/from shakenfist.protos import ${importname}/g" ${item}
done

for item in *_grpc.py; do
    importname=$(echo ${item} | sed 's/_grpc.py//')
    echo "Correcting ${importname} import in ${item}..."
    sed -i '' "s/import ${importname}/from shakenfist.protos import ${importname}/g" ${item}
done