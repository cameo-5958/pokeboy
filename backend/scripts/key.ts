/**
 * Local key management CLI (no admin token needed - you already own the box).
 *
 *   npm run key:new -- "iphone"     mint a key labelled "iphone"
 *   npm run key:list                list issued keys (masked)
 *   npm run key:revoke -- <key>     revoke a key by its full value
 */

import { generateKey, listKeys, maskKey, revokeKey } from "../src/keys.js";

const [cmd, arg] = process.argv.slice(2);

async function main() {
  switch (cmd) {
    case "new": {
      const record = await generateKey(arg || "device");
      console.log("\nNew API key - copy it into the app's Settings on your phone.");
      console.log("(Store it now; the full value is not shown again.)\n");
      console.log(`  ${record.key}\n`);
      console.log(`  label:   ${record.label}`);
      console.log(`  created: ${record.createdAt}\n`);
      break;
    }
    case "list": {
      const keys = await listKeys();
      if (keys.length === 0) {
        console.log("No keys issued yet. The API is open until you mint one.");
        break;
      }
      for (const k of keys) {
        console.log(`  ${maskKey(k.key).padEnd(12)} ${k.label.padEnd(16)} ${k.createdAt}`);
      }
      break;
    }
    case "revoke": {
      if (!arg) {
        console.error("Usage: npm run key:revoke -- <full-key>");
        process.exit(1);
      }
      console.log((await revokeKey(arg)) ? "Revoked." : "Key not found.");
      break;
    }
    default:
      console.error("Usage: npm run key:<new|list|revoke> [-- <label|key>]");
      process.exit(1);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
