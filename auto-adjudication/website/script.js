console.log("✅ script.js has loaded");

const API_BASE = "https://hugknikztl.execute-api.us-east-1.amazonaws.com"; // no trailing slash

document.getElementById('upload-form').addEventListener('submit', async (e) => {
  console.log("🔔 upload-form submit handler fired");
  e.preventDefault();

  const fileInput = document.getElementById('file-input');
  const status = document.getElementById('status');
  if (!fileInput.files.length) {
    return status.innerText = 'Please select a file.';
  }

  const file = fileInput.files[0];

  // 1) Get pre‑signed URL, including the file's MIME type
  const presignResp = await fetch(
    `${API_BASE}/presigned-url` +
    `?filename=${encodeURIComponent(file.name)}` +
    `&contentType=${encodeURIComponent(file.type)}`
  );
  if (!presignResp.ok) {
    console.error("Error fetching presigned URL:", await presignResp.text());
    return status.innerText = 'Error getting upload URL.';
  }
  const { uploadUrl } = await presignResp.json();
  console.log("Received uploadUrl:", uploadUrl);

  // 2) PUT to S3 with the same Content-Type
  const putResp = await fetch(uploadUrl, {
    method: 'PUT',
    headers: {
      'Content-Type': file.type
    },
    body: file
  });

  // 3) Read and log the response text for debugging
  const text = await putResp.text();
  console.log("PUT response status:", putResp.status);
  console.log("PUT response body:", text);

  // 4) Update UI
  if (putResp.ok) {
    status.innerText = '✅ File uploaded successfully!';
  } else {
    status.innerText = `❌ Upload failed (${putResp.status}). Check console for S3 error.`;
  }
});
