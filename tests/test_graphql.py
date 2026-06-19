from opendomainmcp.ingest.graphql import looks_like_graphql, split_graphql

_SDL = """
# The user-facing schema.
scalar DateTime

"An account in the system."
type User {
  id: ID!
  name: String!
  createdAt: DateTime
}

enum Role {
  ADMIN
  MEMBER
}

input CreateUserInput {
  name: String!
  role: Role = MEMBER
}

union SearchResult = User | Role

type Query {
  user(id: ID!): User
  users(role: Role): [User!]!
}

type Mutation {
  createUser(input: CreateUserInput!): User!
}
"""


def test_looks_like_graphql():
    assert looks_like_graphql(_SDL)
    assert not looks_like_graphql("just some prose, no schema here")
    assert not looks_like_graphql("")


def test_split_graphql_one_chunk_per_definition_and_root_field():
    chunks = split_graphql(_SDL, "schema.graphql")
    by_symbol = {c.symbol: c for c in chunks}

    # scalar, type User, enum Role, input CreateUserInput, union SearchResult,
    # plus Query.user, Query.users, Mutation.createUser.
    assert len(chunks) == 8

    # One chunk per top-level definition.
    assert "DateTime" in by_symbol
    assert "User" in by_symbol
    assert "Role" in by_symbol
    assert "CreateUserInput" in by_symbol
    assert "SearchResult" in by_symbol

    # Root operation types are exploded into one chunk per field.
    assert "Query.user" in by_symbol
    assert "Query.users" in by_symbol
    assert "Mutation.createUser" in by_symbol
    # The whole-type symbols for root types are NOT emitted.
    assert "Query" not in by_symbol
    assert "Mutation" not in by_symbol

    # Definition bodies survive intact.
    assert "name: String!" in by_symbol["User"].text
    assert "ADMIN" in by_symbol["Role"].text


def test_split_graphql_preclassifies_as_api():
    chunks = split_graphql(_SDL, "schema.graphql")
    assert chunks
    assert all(c.knowledge.knowledge_type == "API" for c in chunks)
    assert all(c.knowledge.confidence == 1.0 for c in chunks)


def test_ingest_graphql_file_classifies_as_api(pipeline, store, tmp_path):
    schema_file = tmp_path / "schema.graphql"
    schema_file.write_text(_SDL)
    report = pipeline.ingest_path(str(schema_file))
    assert report.files_indexed == 1

    items = store.get_items(limit=100)
    assert items and all(i["metadata"].get("knowledge_type") == "API" for i in items)
    # GraphQL definitions are pre-classified, so the LLM extractor must not run.
    assert pipeline._extractor.calls == 0
