# Dedicated non-base scanner image for Nexa Care
# Inherits from exact approved immutable ClamAV 1.5.5 Debian base digest
FROM clamav/clamav:stable-debian@sha256:d1d22e1cf0be19f3b0d58347dded3c28122e0331dcdca65d5829f64d25eb35fb

# Apply official Debian security updates (e.g., glibc fixes)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Synchronize fresh virus signatures during the controlled image build.
# Fails the image build if fresh official definitions cannot be fetched.
RUN freshclam --stdout
