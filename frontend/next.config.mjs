const nextConfig = {
  async rewrites() {
    return [{ source: '/api/:path*', destination: 'http://localhost:8791/api/:path*' }];
  },
};
export default nextConfig;
