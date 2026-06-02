#pragma once
// This file is designed to break the regex parser.
// class FakeClassInComment should NOT be detected.

#include "Engine.hxx"

const char* kHint = "class StringLiteralClass is also fake";

class Outer {
public:
    void DoWork();

    // A nested class — regex flattens this, tree-sitter keeps the hierarchy
    class Inner {
        void innerMethod();
        int value;          // not m_ prefixed → regex misses it
    };

private:
    Engine* engine;         // not m_ prefixed → regex misses this member too
};

// A lambda assigned to a variable — regex has no idea this is a function
auto handler = [](int x) -> int { return x * 2; };
