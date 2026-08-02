# Legacy deployment manifests

`render.remote-pilot.yaml` is retained only as historical evidence of the
earlier remote-provider pilot configuration. It is not an approved Milestone 6
deployment manifest, must not be used for AWS Textract qualification, and
contains obsolete extraction-provider, envelope-encryption, credential, and
hosting assumptions.

The approved Milestone 6 qualification backend target is Amazon ECS Fargate.
Restoring this manifest to the repository root, enabling it, or adapting Render
for the qualification requires a separate security and deployment review.
