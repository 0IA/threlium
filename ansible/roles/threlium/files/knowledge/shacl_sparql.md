# SHACL-SPARQL Reference

## Core Concepts

SHACL-SPARQL allows defining constraints using SPARQL SELECT queries within SHACL shapes.

## sh:sparql Constraint

```turtle
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .  # required for ^^xsd:anyURI below
@prefix ex:  <http://example.org/> .

ex:PersonAgeConstraint a sh:NodeShape ;
  sh:targetClass ex:Person ;
  sh:sparql [
    sh:prefixes [
      sh:declare [ sh:prefix "ex" ; sh:namespace "http://example.org/"^^xsd:anyURI ]
    ] ;
    sh:select """
      SELECT $this (ex:age AS ?path) (?age AS ?value)
      WHERE {
        $this ex:age ?age .
        FILTER (?age < 0)
      }
    """ ;
    sh:message "Age must be non-negative (found {?value})" ;
  ] .
```

## Key Variables

- `$this` — the focus node being validated (bound by the engine)
- `?path` — property path for the violation report
- `?value` — the violating value

## sh:prefixes

Declare prefix bindings explicitly with `sh:declare`. This is the portable form —
the SHACL spec types each `sh:namespace` as `^^xsd:anyURI`. Two things follow:

- If you write the `^^xsd:anyURI` typed literal, the surrounding Turtle document
  **must** declare the `xsd:` prefix, otherwise rdflib raises a hard
  `BadSyntax ... Prefix "xsd:" not bound` at parse time (verified).
- pySHACL 0.31.0 is actually lenient and also accepts a plain string or
  `^^xsd:string` for `sh:namespace`, but `^^xsd:anyURI` is the spec-correct and
  portable choice.

```turtle
sh:prefixes [
  sh:declare [ sh:prefix "ex"  ; sh:namespace "http://example.org/"^^xsd:anyURI ] ;
  sh:declare [ sh:prefix "rdf" ; sh:namespace "http://www.w3.org/1999/02/22-rdf-syntax-ns#"^^xsd:anyURI ] ;
] ;
```

### How pySHACL actually resolves prefixes (verified, v0.31.0)

`pyshacl/helper/sparql_query_helper.py::collect_prefixes` gathers `sh:declare`
triples only when a `sh:sparql` node has a `sh:prefixes` value. It then merges
declares from: the referenced node, the named graph, and **any `owl:Ontology`
node** in the shapes graph. So the compact form `sh:prefixes ex:` does work when
`ex:` is an ontology node carrying `sh:declare`.

On top of that, the constraint query is executed via `graph.query` against the
**data graph**, whose `@prefix` namespace bindings act as a fallback. Observed
behavior:

- A prefix used in the query resolves if it is in `sh:declare` **OR** bound as a
  namespace in the data graph. Example: `ex:` resolves even with no `sh:declare`
  at all, because the data file declared `@prefix ex:`.
- A prefix bound nowhere (no `sh:declare`, not in the data graph) raises a runtime
  error `Unknown namespace prefix : <pfx>` — it fails loudly, not silently.

This namespace fallback is pySHACL/rdflib-specific; other SHACL engines may not do
it. So for portability still prefer the explicit `sh:declare` block above — but the
earlier claim that the compact form "silently breaks" is not accurate for pySHACL.

## Common Patterns

### Uniqueness constraint
```sparql
SELECT $this (ex:id AS ?path) (?id AS ?value)
WHERE {
  $this ex:id ?id .
  ?other ex:id ?id .
  FILTER ($this != ?other)
}
```

### Cross-property validation
```sparql
SELECT $this (ex:endDate AS ?path) (?end AS ?value)
WHERE {
  $this ex:startDate ?start .
  $this ex:endDate ?end .
  FILTER (?end < ?start)
}
```

### Cardinality with SPARQL
```sparql
SELECT $this (ex:email AS ?path) (COUNT(?email) AS ?value)
WHERE {
  $this ex:email ?email .
}
GROUP BY $this
HAVING (COUNT(?email) > 3)
```

## Rules vs. Recommendations

Required by syntax / spec:

1. **Resolvable prefixes** — every prefix in the SPARQL query must resolve: via `sh:prefixes`/`sh:declare`, or (pySHACL-specific fallback) via a namespace bound in the data graph. A prefix bound nowhere raises a runtime `Unknown namespace prefix` error. For portability, declare them explicitly.
2. **Triple-quoted query string** — wrap the SPARQL text in `"""..."""` inside Turtle.
3. **`$this` is the focus node** — the engine binds `$this` to the focus node; the validating query is evaluated relative to it.

Recommended for a clean pySHACL report (not strictly enforced ordering):

4. **`$this` typically appears in `WHERE`** — the common pattern joins `$this` into the graph pattern so violations are tied to the focus node; this is a convention, not a fixed column-order rule.
5. **Service variables `?path` / `?value`** — returning these (alongside `$this`) is the typical shape; they populate `sh:resultPath` / `sh:value` in the report. Their presence and labels matter more than positional order.
6. **`sh:message` interpolation** — use `{?varname}` for variable substitution in messages.

## Built-in Target Types

- `sh:targetClass` — all instances of a class
- `sh:targetNode` — specific node(s)
- `sh:targetSubjectsOf` — all subjects of a property
- `sh:targetObjectsOf` — all objects of a property

## pySHACL Specifics

- Supports SHACL-SPARQL (sh:sparql) out of the box
- Does NOT support `sh:js` (JavaScript) constraints
- Ontology graph (owl:imports, rdfs:subClassOf) can be passed separately for inference
- Advanced: `sh:deactivated true` to skip a shape during validation

## End-to-End Validation (Python)

Parse the data and shapes graphs separately, then call `validate(...)`. It returns a
triple `(conforms, results_graph, results_text)` — a boolean, the report as an RDF
graph, and a human-readable text report:

```python
from rdflib import Graph
from pyshacl import validate

data_graph = Graph().parse(data=data_ttl, format="turtle")
shapes_graph = Graph().parse(data=shapes_ttl, format="turtle")

conforms, results_graph, results_text = validate(
    data_graph,
    shacl_graph=shapes_graph,
    advanced=True,   # for SHACL Advanced Features (sh:rule, custom targets, functions)
)

print(conforms)
print(results_text)
```

> Verified with pyshacl 0.31.0 / rdflib 7.6.0: plain `sh:sparql` constraint
> components work **without** `advanced=True` (it returned the same 2 violations in
> both modes). `advanced=True` only enables SHACL Advanced Features — SPARQL-based
> rules (`sh:rule`), custom targets (`sh:target` + `sh:select`), and
> `sh:SPARQLFunction`. Keep it on if you mix those in.

## Worked Example: Wolf, Goat & Cabbage

A small state-machine model validated with both plain SHACL (cardinality, goal state)
and SHACL-SPARQL (state safety, legal moves). The valid solution is 7 crossings and
always starts by moving the goat.

`data.ttl` — states `ex:s0..ex:s7` linked by `ex:next`, each carrying
`ex:farmerSide / ex:wolfSide / ex:goatSide / ex:cabbageSide` (each `ex:Left` or `ex:Right`).

`shapes.ttl`:

```turtle
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:  <http://example.org/> .

# Structural: every State has exactly one side per actor.
ex:StateShape a sh:NodeShape ;
  sh:targetClass ex:State ;
  sh:property [ sh:path ex:farmerSide  ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path ex:wolfSide    ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path ex:goatSide    ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path ex:cabbageSide ; sh:minCount 1 ; sh:maxCount 1 ] .

# Safety: wolf+goat or goat+cabbage alone (without the farmer) is forbidden.
ex:SafeStateShape a sh:NodeShape ;
  sh:targetClass ex:State ;
  sh:sparql [
    sh:message "Unsafe state: wolf/goat or goat/cabbage without farmer." ;
    sh:prefixes [
      sh:declare [ sh:prefix "ex" ; sh:namespace "http://example.org/"^^xsd:anyURI ]
    ] ;
    sh:select """
      SELECT $this
      WHERE {
        $this ex:farmerSide ?f ; ex:wolfSide ?w ; ex:goatSide ?g ; ex:cabbageSide ?c .
        FILTER ( (?w = ?g && ?f != ?w) || (?g = ?c && ?f != ?g) )
      }
    """ ;
  ] .

# Transition: farmer must switch banks, move at most one cargo, and only cargo
# that was on the farmer's side may move.
ex:LegalMoveShape a sh:NodeShape ;
  sh:targetSubjectsOf ex:next ;
  sh:sparql [
    sh:message "Illegal move to next state." ;
    sh:prefixes [
      sh:declare [ sh:prefix "ex" ; sh:namespace "http://example.org/"^^xsd:anyURI ]
    ] ;
    sh:select """
      SELECT $this (?next AS ?value)
      WHERE {
        $this ex:next ?next ;
              ex:farmerSide ?f1 ; ex:wolfSide ?w1 ; ex:goatSide ?g1 ; ex:cabbageSide ?c1 .
        ?next  ex:farmerSide ?f2 ; ex:wolfSide ?w2 ; ex:goatSide ?g2 ; ex:cabbageSide ?c2 .
        BIND( IF(?w1 != ?w2, 1, 0) + IF(?g1 != ?g2, 1, 0) + IF(?c1 != ?c2, 1, 0) AS ?cargoDiff )
        FILTER(
          ?f1 = ?f2 ||
          ?cargoDiff > 1 ||
          (?w1 != ?w2 && ?w1 != ?f1) ||
          (?g1 != ?g2 && ?g1 != ?f1) ||
          (?c1 != ?c2 && ?c1 != ?f1)
        )
      }
    """ ;
  ] .

# Goal: the final node must have everyone on the right bank.
ex:GoalShape a sh:NodeShape ;
  sh:targetNode ex:s7 ;
  sh:property [ sh:path ex:farmerSide  ; sh:hasValue ex:Right ] ;
  sh:property [ sh:path ex:wolfSide    ; sh:hasValue ex:Right ] ;
  sh:property [ sh:path ex:goatSide    ; sh:hasValue ex:Right ] ;
  sh:property [ sh:path ex:cabbageSide ; sh:hasValue ex:Right ] .
```

Verified run (pyshacl 0.31.0): the correct 7-state path gives `Conforms: True`.
Breaking the first move (carry the wolf instead of the goat at `ex:s1`) yields
`Conforms: False` with **2** violations on focus node `ex:s1` —
`ex:LegalMoveShape` (the wolf moved while it was not on the farmer's side) and
`ex:SafeStateShape` (goat + cabbage left alone). Both `sh:sparql` constraints fire
without `advanced=True`.
