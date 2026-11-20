// MongoDB init script for Website Analyzer.
// Executed automatically on the FIRST start of the container (empty data directory).
// Stores broken links per scan; one document per MySQL scan:
//   { _id, my_sql_scan_id: <scans.id>, broken_links: [ ... ] }

const dbName = process.env.MONGO_DB;
const collName = process.env.MONGO_CL;

const target = db.getSiblingDB(dbName);
target.createCollection(collName);
target.getCollection(collName).createIndex(
  { my_sql_scan_id: 1 },
  { name: "idx_my_sql_scan_id" }
);