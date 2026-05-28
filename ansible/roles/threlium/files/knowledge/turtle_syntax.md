# Turtle Syntax Reference

## Prefixes

```turtle
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <http://example.org/> .
```

SPARQL-style prolog (no leading `@`, no trailing dot). This is the SPARQL 1.1 form
used inside queries — do not mix it with the classic Turtle `@prefix ... .` form above:
```sparql
PREFIX ex: <http://example.org/>
```

## Triples

```turtle
ex:Alice a ex:Person ;          # rdf:type shorthand
  ex:name "Alice" ;             # string literal
  ex:age 30 ;                   # integer (xsd:integer)
  ex:height 1.65 ;              # decimal
  ex:active true ;              # boolean
  ex:birthDate "1994-03-15"^^xsd:date ;  # typed literal
  ex:knows ex:Bob .             # URI object
```

## Predicate-Object Lists (`;`)

Multiple predicates for same subject:
```turtle
ex:Alice ex:name "Alice" ;
         ex:age 30 ;
         ex:knows ex:Bob .
```

## Object Lists (`,`)

Multiple objects for same subject-predicate:
```turtle
ex:Alice ex:knows ex:Bob, ex:Charlie, ex:Dave .
```

## Blank Nodes

Anonymous (inline):
```turtle
ex:Alice ex:address [ ex:city "Berlin" ; ex:zip "10115" ] .
```

Named:
```turtle
_:addr1 ex:city "Berlin" .
ex:Alice ex:address _:addr1 .
```

## Collections (RDF Lists)

```turtle
ex:Colors ex:members ( "red" "green" "blue" ) .
```

## Multiline Strings

```turtle
ex:doc ex:content """
  This is a
  multiline string.
""" .
```

## Language Tags

```turtle
ex:Berlin ex:name "Berlin"@en, "Берлін"@uk, "Берлин"@ru .
```

## Common Datatypes

| Datatype | Example |
|----------|---------|
| `xsd:string` | `"hello"` (default) |
| `xsd:integer` | `42` |
| `xsd:decimal` | `3.14` |
| `xsd:float` | `"3.14"^^xsd:float` |
| `xsd:boolean` | `true`, `false` |
| `xsd:date` | `"2024-01-15"^^xsd:date` |
| `xsd:dateTime` | `"2024-01-15T10:30:00Z"^^xsd:dateTime` |
| `xsd:anyURI` | `"http://example.org/"^^xsd:anyURI` |

## Common Mistakes

1. **Missing final dot** — every statement must end with `.`
2. **Semicolons before dot** — last predicate-object pair uses `.` not `;`
3. **Unescaped special chars** — use `\n`, `\t`, `\\`, `\"` in strings
4. **Prefix not declared** — every `prefix:local` must have a matching `@prefix`
5. **Angle brackets for full URIs** — `<http://...>` not `http://...`
6. **`a` is only for `rdf:type`** — cannot use as a general predicate
