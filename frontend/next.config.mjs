/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emits a minimal self-contained server bundle, which keeps the Docker image small.
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
