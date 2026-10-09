import http from "node:http";
import { betterAuth } from "better-auth";
import { toNodeHandler } from "better-auth/node";
import { LibsqlDialect } from "@libsql/kysely-libsql";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));

export const auth = betterAuth({
  database: {
    dialect: new LibsqlDialect({ url: `file:${path.join(dir, "auth.db")}` }),
    type: "sqlite",
  },
  basePath: "/api/auth",
  secret: process.env.BETTER_AUTH_SECRET || "crossfire-dev-secret",
  emailAndPassword: { enabled: true, requireEmailVerification: false },
});

const handler = toNodeHandler(auth.handler);
const server = http.createServer((req, res) => {
  if (req.url.startsWith("/api/auth")) return handler(req, res);
  res.writeHead(404).end();
});
const port = Number(process.env.AUTH_PORT || 3922);
server.listen(port, "127.0.0.1", () => console.log(`better-auth on :${port}`));
