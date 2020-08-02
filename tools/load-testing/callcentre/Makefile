# Callcentre - Shaken Fist Load tester makefile
#
# For linter installation (in CI) see:
# 	https://golangci-lint.run/usage/install/#ci-installation
#


GOFMT_FILES?=$$(find . -name '*.go' |grep -v vendor)
TEST?=./$(PKG_NAME)/...
TEST_COUNT?=1

default: build


build: fmtcheck
	@echo "==> Building..."
	go build .

test: fmtcheck
	go test $(TEST) $(TESTARGS) -v -timeout=120s -parallel=4

fmt:
	@echo "==> Fixing source code with gofmt..."
	@sh -c "find . -name '*.go' | grep -v vendor | xargs gofmt -l -w"

fmtcheck:
	@sh -c "find . -name '*.go' | grep -v vendor | xargs gofmt -l -s"

lint:
	@golangci-lint run ./...

install-tools:
	GO111MODULE=on go install github.com/golangci/golangci-lint/cmd/golangci-lint


.PHONY: build lint test fmt fmtcheck lint install-tools

