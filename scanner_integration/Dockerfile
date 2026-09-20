# Dedicated non-base scanner image for Nexa Care
# Inherits from exact approved immutable ClamAV 1.4.6 Debian base digest
FROM clamav/clamav:1.4.6-debian@sha256:2463487b937071421c0ddadac6616111bedba45b9bd9312bbb4591b57450f8f4

# Synchronize fresh virus signatures during the controlled image build.
# Fails the image build if fresh official definitions cannot be fetched.
RUN freshclam --stdout
