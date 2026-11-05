# Meta SAM 3.1 external-result workflow

## Accepted factual scope

One owner-operated external API request sent a synthetic truck image to SAM 3.1. The raw streamed output was retained privately. Zonivra imported the saved bytes offline at commit `de8a08afb8286327b446a91b26178932e4f0f81a` and displayed the review record.

The bounded independent review accepted only this observed response profile and workflow. It verified:

- source and response hashes matched the declared bytes;
- the saved stream matched the strict observed profile;
- source-image linkage and box bounds were checked;
- one genuine bounding box was displayed;
- the segment remained `UNCLASSIFIED`;
- no confidence or risk was invented;
- mask structure was checked without raster rendering;
- canonical RF-DETR observations and Risk Kernel decisions were unchanged.

## Not demonstrated

- universal compatibility with SAM 3.1 responses;
- provider authenticity or cryptographic signature;
- native in-app Meta inference;
- industrial-video analysis by Meta;
- segmentation accuracy, class accuracy, mask correctness, or safety performance.

## Public-candidate boundary

Private response bytes, request material, and authorization records are excluded. The candidate contains only a labelled synthetic fixture for offline UI reproduction. Sanitized audiovisual evidence may be reviewed separately under its own provenance record.

