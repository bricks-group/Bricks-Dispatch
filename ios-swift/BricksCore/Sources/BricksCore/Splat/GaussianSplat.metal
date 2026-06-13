#include <metal_stdlib>
using namespace metal;

// ---------------------------------------------------------------------------
// Data types matching SplatPoint's in-memory layout (Swift side feeds these
// directly from the sorted splat buffer — 32 bytes per instance).
// ---------------------------------------------------------------------------

struct SplatIn {
    packed_float3 position;   // 12 bytes
    packed_float3 log_scale;  // 12 bytes
    uchar4        color_rgba; //  4 bytes  (R G B A, 0-255)
    uchar4        rot_wxyz;   //  4 bytes  (w x y z mapped 0-255 → [-1, 1])
};

struct Uniforms {
    float4x4 view_proj;  // projection * view
    float4x4 view;       // view matrix (world → camera)
    float2   focal;      // (fx, fy) in pixels
    float2   viewport;   // (width, height) in pixels
};

struct VertexOut {
    float4 position [[position]];
    float2 uv;       // position within Gaussian ellipse in σ-units, range ≈ [-3, 3]
    float4 color;    // pre-divided to [0, 1]
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Quaternion (x,y,z,w) → 3×3 column-major rotation matrix
static float3x3 quat_to_mat(float4 q) {
    float x = q.x, y = q.y, z = q.z, w = q.w;
    return float3x3(
        float3(1.0 - 2.0*(y*y + z*z),       2.0*(x*y + w*z),       2.0*(x*z - w*y)),
        float3(      2.0*(x*y - w*z), 1.0 - 2.0*(x*x + z*z),       2.0*(y*z + w*x)),
        float3(      2.0*(x*z + w*y),       2.0*(y*z - w*x), 1.0 - 2.0*(x*x + y*y))
    );
}

// ---------------------------------------------------------------------------
// Vertex shader
// Each splat is rendered as a triangle-strip quad (vertexID 0-3).
// instanceID selects the splat from the sorted buffer.
// ---------------------------------------------------------------------------

vertex VertexOut splat_vertex(
    uint            vid     [[vertex_id]],
    uint            iid     [[instance_id]],
    constant SplatIn* splats [[buffer(0)]],
    constant Uniforms& uni   [[buffer(1)]]
) {
    // Quad corners — triangle strip: (0,1,2), (1,3,2)
    const float2 corners[4] = {
        float2(-1.0, -1.0),
        float2( 1.0, -1.0),
        float2(-1.0,  1.0),
        float2( 1.0,  1.0)
    };

    SplatIn s = splats[iid];

    // -----------------------------------------------------------------------
    // Decode rotation quaternion: 0-255 → [-1, 1], then normalize
    // Stored as w,x,y,z; quat_to_mat expects x,y,z,w
    // -----------------------------------------------------------------------
    float4 q_wxyz = (float4(s.rot_wxyz) / 127.5) - 1.0;
    q_wxyz = normalize(q_wxyz);
    float4 q = float4(q_wxyz.y, q_wxyz.z, q_wxyz.w, q_wxyz.x); // reorder to x,y,z,w
    float3x3 R = quat_to_mat(q);

    // -----------------------------------------------------------------------
    // 3D covariance Σ = (R·S)·(R·S)ᵀ   where S = diag(exp(log_scale))
    // -----------------------------------------------------------------------
    float3 sc = exp(float3(s.log_scale));
    // Column-scale R: each column i multiplied by sc[i]
    float3x3 RS = float3x3(R[0] * sc.x, R[1] * sc.y, R[2] * sc.z);
    float3x3 Sigma3D = RS * transpose(RS);

    // -----------------------------------------------------------------------
    // Project to view space
    // -----------------------------------------------------------------------
    float4 p_view = uni.view * float4(float3(s.position), 1.0);
    float  tx = p_view.x, ty = p_view.y, tz = p_view.z;

    // Cull splats behind the near plane
    if (tz > -0.1) {
        VertexOut out;
        out.position = float4(0.0, 0.0, -2.0, 1.0); // degenerate — clipped
        out.uv    = float2(0.0);
        out.color = float4(0.0);
        return out;
    }

    float tz2 = tz * tz;

    // Jacobian of perspective projection at p_view
    float3x3 J = float3x3(
        float3(uni.focal.x / tz,             0.0, -uni.focal.x * tx / tz2),
        float3(           0.0, uni.focal.y / tz, -uni.focal.y * ty / tz2),
        float3(           0.0,             0.0,                       0.0)
    );

    // View rotation (upper-left 3×3 of view matrix)
    float3x3 W = float3x3(
        uni.view.columns[0].xyz,
        uni.view.columns[1].xyz,
        uni.view.columns[2].xyz
    );

    // 2D projected covariance: Σ' = J·W·Σ·Wᵀ·Jᵀ   (upper-left 2×2)
    float3x3 JW       = J * W;
    float3x3 Sigma2D3 = JW * Sigma3D * transpose(JW);

    // Extract 2×2 (add low-pass 0.3 to avoid degenerate splats)
    float a = Sigma2D3[0][0] + 0.3;
    float b = Sigma2D3[1][0];          // = Sigma2D3[0][1] (symmetric)
    float c = Sigma2D3[1][1] + 0.3;

    // -----------------------------------------------------------------------
    // Eigendecomposition of the 2×2 covariance → ellipse semi-axes
    // -----------------------------------------------------------------------
    float mid  = 0.5 * (a + c);
    float disc = sqrt(max(0.01, 0.25*(a - c)*(a - c) + b*b));
    float lam1 = mid + disc;  // larger eigenvalue
    float lam2 = mid - disc;  // smaller eigenvalue

    // Semi-axes in screen pixels at 3σ
    float r1 = 3.0 * sqrt(max(0.0, lam1));
    float r2 = 3.0 * sqrt(max(0.0, lam2));

    // Eigenvector for λ1
    float2 ev1 = normalize(float2(b, lam1 - a));  // may be (1,0) when b=0
    float2 ev2 = float2(-ev1.y, ev1.x);

    // -----------------------------------------------------------------------
    // Screen-space quad corner
    // -----------------------------------------------------------------------
    float2 corner      = corners[vid];
    float2 px_offset   = corner.x * r1 * ev1 + corner.y * r2 * ev2;

    // NDC of the splat center
    float4 clip_center = uni.view_proj * float4(float3(s.position), 1.0);
    float2 ndc_center  = clip_center.xy / clip_center.w;

    // Delta in NDC
    float2 delta_ndc = px_offset / (uni.viewport * 0.5);

    VertexOut out;
    out.position = float4(ndc_center + delta_ndc, clip_center.z / clip_center.w, 1.0);

    // Pass corner in σ units so fragment can evaluate the Gaussian
    // corner ∈ [-1, 1] → multiply by 3 to be in 3σ units
    out.uv    = corner * 3.0;
    out.color = float4(s.color_rgba) / 255.0;
    return out;
}

// ---------------------------------------------------------------------------
// Fragment shader
// Evaluates the Gaussian falloff and outputs pre-multiplied RGBA.
// ---------------------------------------------------------------------------

fragment float4 splat_fragment(VertexOut in [[stage_in]]) {
    // Gaussian: exp(-0.5 * |uv|²) where uv is in σ-units
    float d     = dot(in.uv, in.uv);
    float gauss = exp(-0.5 * d);

    float alpha = in.color.a * gauss;
    if (alpha < 1.0 / 255.0) discard_fragment();

    // Pre-multiplied alpha output for ONE / ONE_MINUS_SRC_ALPHA blending
    return float4(in.color.rgb * alpha, alpha);
}
