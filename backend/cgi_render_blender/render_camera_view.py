"""Fixed Blender render script for cgi_render.py's render_camera_view tool.

Runs inside `blender --background --python` and takes one JSON argument
after `--` (validated by cgi_render._parse_render_params). It only sets
camera and render properties through bpy; it never evaluates anything
from its arguments or from the loaded scene.
"""
import json
import sys

import bpy
from mathutils import Vector


def main() -> None:
    params = json.loads(sys.argv[sys.argv.index("--") + 1])
    scene = bpy.context.scene

    cam_data = bpy.data.cameras.new("BricksRenderCamera")
    cam_data.lens = float(params["lens_mm"])
    cam = bpy.data.objects.new("BricksRenderCamera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = Vector([float(v) for v in params["camera_location"]])
    direction = Vector([float(v) for v in params["camera_target"]]) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(params["samples"])
    # Debian's packaged Blender is built without OpenImageDenoise, and Cycles
    # aborts the render if denoising is requested without it.
    scene.cycles.use_denoising = bool(getattr(bpy.app.build_options, "openimagedenoise", False))
    # OSL script nodes in a scene are compiled and run by Cycles; keep them off.
    scene.cycles.shading_system = False
    scene.render.resolution_x = int(params["resolution_x"])
    scene.render.resolution_y = int(params["resolution_y"])
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(params["output_path"])
    bpy.ops.render.render(write_still=True)


main()
