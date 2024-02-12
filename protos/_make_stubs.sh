#!/bin/bash

# Run this from directory containing the generated gRPC code

python -m grpc_tools.protoc -I../protos --python_out=. --pyi_out=. \
    --grpc_python_out=. $(find ../protos -name *.proto)

# This is terrible, but gRPC lacks a python_package option, so we have to
# tweak the imports in the _grpc.py files.
for item in *_grpc.py; do
    importname=$(echo $item | sed 's/_grpc.py//')
    echo "Correcting ${importname} import in ${item}..."
    cat ${item} | sed "s/import ${importname}/from shakenfist import ${importname}/g" > ${item}.new
    mv ${item}.new ${item}
done