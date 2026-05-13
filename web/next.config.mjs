import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Output a standalone build so the production Docker image only needs
  // the ``.next/standalone`` directory to run — drops final image size
  // dramatically.
  output: "standalone",
  reactStrictMode: true,

  // Belt-and-suspenders: also wire the @/ alias through webpack
  // explicitly. Next 15 *should* honour tsconfig paths but inside the
  // Alpine build container something about the lookup wasn't picking
  // them up; an explicit alias at the bundler level always works.
  webpack: (config) => {
    config.resolve = config.resolve || {};
    config.resolve.alias = {
      ...(config.resolve.alias || {}),
      "@": path.resolve(__dirname),
    };
    return config;
  },

  // Proxy /api/* to the FastAPI backend.
  //
  // CAREFUL: Next.js evaluates rewrites at BUILD time for `output: standalone`
  // and bakes the destination into the standalone server bundle. The runtime
  // value of API_URL is NOT consulted. So the fallback below must be a
  // sensible default for the most common deployment shape — compose, where
  // the API service is reachable at `http://api:8000` via Docker DNS. If
  // you build locally and want it to hit localhost, set API_URL=http://localhost:8000
  // when running `npm run build` (or pass it as a build-arg to Docker).
  async rewrites() {
    const api = process.env.API_URL || "http://api:8000";
    return [
      { source: "/api/:path*", destination: `${api}/:path*` },
    ];
  },
};

export default nextConfig;
