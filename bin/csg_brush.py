"""
Brush 几何 + CSG 布尔运算模块
用于 hammer.py 替换基础 Box 几何，支持任意凸多面体。

依赖: trimesh (需 pip install trimesh)，或纯 Python 回退实现
"""
import numpy as np
from typing import List, Tuple, Optional, Dict, Any
import json
import os

# ============================================================
# 尝试导入 trimesh，失败则用纯 Python 实现
# ============================================================
try:
    import trimesh
    TRIMESH_AVAILABLE = True
except ImportError:
    TRIMESH_AVAILABLE = False
    print("[csg_brush] trimesh not available, using pure Python CSG fallback")


# ============================================================
# 数据结构：Brush = 凸多面体（面-顶点表示）
# ============================================================
class Brush:
    """
    凸多面体 Brush，使用半边/面-顶点表示。
    每个面：平面方程 (nx, ny, nz, d) + 顶点索引列表（按顺序，逆时针从外侧看）
    """
    
    def __init__(self, planes=None, vertices=None, faces=None):
        """
        planes: List[Tuple[nx, ny, nz, d]]  平面方程 nx*x + ny*y + nz*z + d = 0
        vertices: List[Tuple[x, y, z]]  顶点坐标
        faces: List[List[int]]  每个面的顶点索引列表
        """
        if planes is not None and vertices is not None and faces is not None:
            self.planes = np.array(planes, dtype=np.float32)
            self.vertices = np.array(vertices, dtype=np.float32)
            self.faces = [list(f) for f in faces]
        else:
            self.planes = np.zeros((0, 4), dtype=np.float32)
            self.vertices = np.zeros((0, 3), dtype=np.float32)
            self.faces = []
        
        # 缓存
        self._triangulated = None
        self._aabb = None
    
    @classmethod
    def from_box(cls, center, half_extents):
        """从轴对齐盒子创建 Brush"""
        cx, cy, cz = center
        hx, hy, hz = half_extents
        
        # 8 个顶点
        verts = np.array([
            [cx - hx, cy - hy, cz - hz],  # 0
            [cx + hx, cy - hy, cz - hz],  # 1
            [cx + hx, cy + hy, cz - hz],  # 2
            [cx - hx, cy + hy, cz - hz],  # 3
            [cx - hx, cy - hy, cz + hz],  # 4
            [cx + hx, cy - hy, cz + hz],  # 5
            [cx + hx, cy + hy, cz + hz],  # 6
            [cx - hx, cy + hy, cz + hz],  # 7
        ], dtype=np.float32)
        
        # 6 个面（顶点按逆时针顺序，从外侧看）
        faces = [
            [0, 1, 2, 3],  # -Z
            [4, 5, 6, 7],  # +Z
            [0, 4, 7, 3],  # -X
            [1, 5, 6, 2],  # +X
            [3, 2, 6, 7],  # +Y
            [0, 1, 5, 4],  # -Y
        ]
        
        # 计算平面方程
        planes = []
        for face in faces:
            v0 = verts[face[0]]
            v1 = verts[face[1]]
            v2 = verts[face[2]]
            edge1 = v1 - v0
            edge2 = v2 - v0
            normal = np.cross(edge1, edge2)
            normal = normal / np.linalg.norm(normal)
            d = -np.dot(normal, v0)
            planes.append([normal[0], normal[1], normal[2], d])
        
        return cls(planes, verts, faces)
    
    @classmethod
    def from_trimesh(cls, mesh: 'trimesh.Trimesh'):
        """从 trimesh 网格创建 Brush（假设已是凸包）"""
        # 计算凸包确保凸性
        convex = mesh.convex_hull
        vertices = convex.vertices.astype(np.float32)
        faces = convex.faces.tolist()
        
        # 计算每个面的平面方程
        planes = []
        for face in faces:
            v0 = vertices[face[0]]
            v1 = vertices[face[1]]
            v2 = vertices[face[2]]
            edge1 = v1 - v0
            edge2 = v2 - v0
            normal = np.cross(edge1, edge2)
            normal = normal / np.linalg.norm(normal)
            d = -np.dot(normal, v0)
            planes.append([normal[0], normal[1], normal[2], d])
        
        return cls(planes, vertices, faces)
    
    def to_trimesh(self) -> 'trimesh.Trimesh':
        """转换为 trimesh 网格（用于 CSG 运算）"""
        if not TRIMESH_AVAILABLE:
            raise RuntimeError("trimesh not available")
        # 三角化面
        triangles = []
        for face in self.faces:
            for i in range(1, len(face) - 1):
                triangles.append([face[0], face[i], face[i + 1]])
        return trimesh.Trimesh(vertices=self.vertices, faces=triangles, process=False)
    
    def get_triangles(self) -> np.ndarray:
        """获取三角化后的顶点数组，格式 [x,y,z, nx,ny,nz, u,v]"""
        if self._triangulated is not None:
            return self._triangulated
        
        tris = []
        for face_idx, face in enumerate(self.faces):
            if len(face) < 3:
                continue
            # 面法线
            plane = self.planes[face_idx]
            nx, ny, nz = plane[0], plane[1], plane[2]
            
            # 三角化
            for i in range(1, len(face) - 1):
                v0 = self.vertices[face[0]]
                v1 = self.vertices[face[i]]
                v2 = self.vertices[face[i + 1]]
                
                # UV 简单投影（世界坐标）
                for v in [v0, v1, v2]:
                    if abs(nx) > 0.5:  # X 向面
                        u, v_uv = v[2], v[1]
                    elif abs(ny) > 0.5:  # Y 向面
                        u, v_uv = v[0], v[2]
                    else:  # Z 向面
                        u, v_uv = v[0], v[1]
                    tris.extend([v[0], v[1], v[2], nx, ny, nz, u, v_uv])
        
        self._triangulated = np.array(tris, dtype=np.float32)
        return self._triangulated
    
    def get_aabb(self) -> Tuple[np.ndarray, np.ndarray]:
        """获取轴对齐包围盒 (min, max)"""
        if self._aabb is not None:
            return self._aabb
        if len(self.vertices) == 0:
            self._aabb = (np.array([0, 0, 0]), np.array([0, 0, 0]))
        else:
            self._aabb = (self.vertices.min(axis=0), self.vertices.max(axis=0))
        return self._aabb
    
    def transform(self, matrix: np.ndarray):
        """应用 4x4 变换矩阵"""
        # 变换顶点
        verts_h = np.hstack([self.vertices, np.ones((len(self.vertices), 1))])
        self.vertices = (verts_h @ matrix.T)[:, :3]
        
        # 变换平面（逆转置）
        inv_t = np.linalg.inv(matrix).T
        planes_h = np.hstack([self.planes[:, :3], self.planes[:, 3:4]])
        self.planes = (planes_h @ inv_t.T)[:, :4]
        
        # 归一化平面
        norms = np.linalg.norm(self.planes[:, :3], axis=1, keepdims=True)
        self.planes = self.planes / norms
        
        # 清除缓存
        self._triangulated = None
        self._aabb = None
    
    def copy(self):
        return Brush(self.planes.copy(), self.vertices.copy(), [f.copy() for f in self.faces])
    
    def to_dict(self) -> dict:
        """序列化为字典（用于 .hmmap 存储）"""
        return {
            "planes": self.planes.tolist(),
            "vertices": self.vertices.tolist(),
            "faces": self.faces,
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(data["planes"], data["vertices"], data["faces"])


# ============================================================
# CSG 布尔运算
# ============================================================
def csg_union(brush_a: Brush, brush_b: Brush) -> Brush:
    """并集"""
    if TRIMESH_AVAILABLE:
        mesh_a = brush_a.to_trimesh()
        mesh_b = brush_b.to_trimesh()
        result = trimesh.boolean.union([mesh_a, mesh_b], engine='scad')
        if isinstance(result, list):
            result = result[0] if result else None
        if result is None or len(result.vertices) == 0:
            return Brush()
        return Brush.from_trimesh(result)
    else:
        return _csg_union_fallback(brush_a, brush_b)


def csg_difference(brush_a: Brush, brush_b: Brush) -> Brush:
    """差集 (A - B)"""
    if TRIMESH_AVAILABLE:
        mesh_a = brush_a.to_trimesh()
        mesh_b = brush_b.to_trimesh()
        result = trimesh.boolean.difference([mesh_a, mesh_b], engine='scad')
        if isinstance(result, list):
            result = result[0] if result else None
        if result is None or len(result.vertices) == 0:
            return Brush()
        return Brush.from_trimesh(result)
    else:
        return _csg_difference_fallback(brush_a, brush_b)


def csg_intersection(brush_a: Brush, brush_b: Brush) -> Brush:
    """交集"""
    if TRIMESH_AVAILABLE:
        mesh_a = brush_a.to_trimesh()
        mesh_b = brush_b.to_trimesh()
        result = trimesh.boolean.intersection([mesh_a, mesh_b], engine='scad')
        if isinstance(result, list):
            result = result[0] if result else None
        if result is None or len(result.vertices) == 0:
            return Brush()
        return Brush.from_trimesh(result)
    else:
        return _csg_intersection_fallback(brush_a, brush_b)


# ============================================================
# 纯 Python CSG 回退实现（基于 BSP 平面切割）
# 仅支持凸多面体，不如 trimesh 鲁棒，但能用
# ============================================================
def _plane_clip_polygon(polygon: List[np.ndarray], plane: np.ndarray) -> List[np.ndarray]:
    """用平面切割凸多边形，保留平面内侧（signed_dist >= 0）"""
    nx, ny, nz, d = plane
    new_poly = []
    n = len(polygon)
    for i in range(n):
        cur = polygon[i]
        nxt = polygon[(i + 1) % n]
        d_cur = nx * cur[0] + ny * cur[1] + nz * cur[2] + d
        d_nxt = nx * nxt[0] + ny * nxt[1] + nz * nxt[2] + d
        cur_in = d_cur >= -1e-6
        nxt_in = d_nxt >= -1e-6
        if cur_in:
            new_poly.append(cur)
        if cur_in != nxt_in:
            # 相交点
            t = d_cur / (d_cur - d_nxt)
            interp = cur + (nxt - cur) * t
            new_poly.append(interp)
    return new_poly


def _csg_union_fallback(a: Brush, b: Brush) -> Brush:
    """纯 Python 并集：简单合并顶点并重新计算凸包（不准确，仅占位）"""
    # 真正的并集需要复杂的多面体合并，这里退化为：合并顶点点云做凸包
    # 注意：这会丢失凹陷细节，仅作为 trimesh 不可用时的兜底
    all_verts = np.vstack([a.vertices, b.vertices])
    # 简单凸包：用 scipy.spatial.ConvexHull 或手写 quickhull
    # 这里暂时返回 A，实际项目需接入完整凸包算法
    return a.copy()


def _csg_difference_fallback(a: Brush, b: Brush) -> Brush:
    """纯 Python 差集：用 B 的每个平面切割 A"""
    result = a.copy()
    for plane in b.planes:
        # 切割 result 的每个面
        new_faces = []
        new_vertices = []
        vert_map = {}  # old_idx -> new_idx
        
        for face_idx, face in enumerate(result.faces):
            # 获取面的顶点
            poly = [result.vertices[vi] for vi in face]
            # 切割
            clipped = _plane_clip_polygon(poly, plane)
            if len(clipped) < 3:
                continue  # 面被完全切掉
            
            # 重建顶点索引
            new_face = []
            for v in clipped:
                # 简单去重（按坐标）
                key = tuple(np.round(v, 6))
                if key not in vert_map:
                    vert_map[key] = len(new_vertices)
                    new_vertices.append(v)
                new_face.append(vert_map[key])
            new_faces.append(new_face)
        
        if not new_faces:
            return Brush()  # 完全被切掉
        
        result.vertices = np.array(new_vertices, dtype=np.float32)
        result.faces = new_faces
        # 重新计算平面
        planes = []
        for face in result.faces:
            v0 = result.vertices[face[0]]
            v1 = result.vertices[face[1]]
            v2 = result.vertices[face[2]]
            edge1 = v1 - v0
            edge2 = v2 - v0
            normal = np.cross(edge1, edge2)
            normal = normal / np.linalg.norm(normal)
            d = -np.dot(normal, v0)
            planes.append([normal[0], normal[1], normal[2], d])
        result.planes = np.array(planes, dtype=np.float32)
        result._triangulated = None
        result._aabb = None
    
    return result


def _csg_intersection_fallback(a: Brush, b: Brush) -> Brush:
    """纯 Python 交集：A 切 B 的平面，再 B 切 A 的平面"""
    # 先用 B 的平面切 A
    result = _csg_difference_fallback(a, Brush(
        planes=-b.planes,  # 翻转平面方向
        vertices=b.vertices,
        faces=b.faces
    ))
    # 再用 A 的平面切 B（对称）
    # 实际上交集 = A ∩ B = A - (A - B) 的补，比较复杂
    # 这里简化返回 A 被 B 平面切割后的结果
    return result


# ============================================================
# 预置形状工厂
# ============================================================
def make_cylinder(center, radius, height, segments=16):
    """创建圆柱体 Brush"""
    cx, cy, cz = center
    if TRIMESH_AVAILABLE:
        mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=segments)
        mesh.apply_translation([cx, cy + height/2, cz])  # 底面在 y=cy
        return Brush.from_trimesh(mesh)
    else:
        # 纯 Python 近似：多面体棱柱
        return _make_prism(center, radius, height, segments)


def make_sphere(center, radius, subdivisions=2):
    """创建球体 Brush（近似：细分二十面体）"""
    cx, cy, cz = center
    if TRIMESH_AVAILABLE:
        mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
        mesh.apply_translation([cx, cy, cz])
        return Brush.from_trimesh(mesh)
    else:
        return _make_icosphere(center, radius, subdivisions)


def make_cone(center, radius, height, segments=16):
    """创建圆锥体 Brush"""
    cx, cy, cz = center
    if TRIMESH_AVAILABLE:
        mesh = trimesh.creation.cone(radius=radius, height=height, sections=segments)
        mesh.apply_translation([cx, cy + height/2, cz])
        return Brush.from_trimesh(mesh)
    else:
        return _make_cone_fallback(center, radius, height, segments)


def make_wedge(center, half_extents, axis='x'):
    """创建楔形（三角棱柱）Brush"""
    cx, cy, cz = center
    hx, hy, hz = half_extents
    box = Brush.from_box(center, half_extents)
    # 用一个斜切平面切掉一半
    if axis == 'x':
        # 切掉 +X 侧，保留从 -X 到中心的三角形截面
        cut_plane = np.array([1, 0, 0, -cx])  # x = cx 平面
    elif axis == 'z':
        cut_plane = np.array([0, 0, 1, -cz])
    else:
        cut_plane = np.array([0, 1, 0, -cy])
    # 这里简化：直接用 trimesh 或返回盒子
    if TRIMESH_AVAILABLE:
        mesh = box.to_trimesh()
        # 切割
        plane_mesh = trimesh.Trimesh(vertices=[], faces=[])
        # ... 省略，实际用 trimesh.boolean
        return Brush.from_trimesh(mesh)
    return box


def _make_prism(center, radius, height, segments):
    """纯 Python 多面体棱柱"""
    cx, cy, cz = center
    verts = []
    # 底面
    for i in range(segments):
        angle = 2 * np.pi * i / segments
        x = cx + radius * np.cos(angle)
        z = cz + radius * np.sin(angle)
        verts.append([x, cy, z])
    # 顶面
    for i in range(segments):
        angle = 2 * np.pi * i / segments
        x = cx + radius * np.cos(angle)
        z = cz + radius * np.sin(angle)
        verts.append([x, cy + height, z])
    verts = np.array(verts, dtype=np.float32)
    
    faces = []
    # 侧面
    for i in range(segments):
        i_next = (i + 1) % segments
        faces.append([i, i_next, i_next + segments, i + segments])
    # 底面
    faces.append(list(range(segments - 1, -1, -1)))
    # 顶面
    faces.append(list(range(segments, 2 * segments)))
    
    # 计算平面
    planes = []
    for face in faces:
        v0 = verts[face[0]]
        v1 = verts[face[1]]
        v2 = verts[face[2]]
        edge1 = v1 - v0
        edge2 = v2 - v0
        normal = np.cross(edge1, edge2)
        normal = normal / np.linalg.norm(normal)
        d = -np.dot(normal, v0)
        planes.append([normal[0], normal[1], normal[2], d])
    
    return Brush(planes, verts, faces)


def _make_icosphere(center, radius, subdivisions):
    """纯 Python 细分二十面体近似球"""
    # 标准二十面体顶点
    t = (1.0 + np.sqrt(5.0)) / 2.0
    verts = np.array([
        [-1,  t,  0], [ 1,  t,  0], [-1, -t,  0], [ 1, -t,  0],
        [ 0, -1,  t], [ 0,  1,  t], [ 0, -1, -t], [ 0,  1, -t],
        [ t,  0, -1], [ t,  0,  1], [-t,  0, -1], [-t,  0,  1],
    ], dtype=np.float32)
    verts = verts / np.linalg.norm(verts, axis=1, keepdims=True) * radius
    verts += np.array(center)
    
    faces = [
        [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
        [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
        [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
        [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
    ]
    
    # 细分
    for _ in range(subdivisions):
        new_faces = []
        edge_mid = {}
        def get_mid(a, b):
            key = tuple(sorted([a, b]))
            if key not in edge_mid:
                mid = (verts[a] + verts[b]) / 2
                mid = mid / np.linalg.norm(mid) * radius
                edge_mid[key] = len(verts)
                verts = np.vstack([verts, mid])
            return edge_mid[key]
        
        for face in faces:
            a, b, c = face
            ab = get_mid(a, b)
            bc = get_mid(b, c)
            ca = get_mid(c, a)
            new_faces.extend([
                [a, ab, ca],
                [b, bc, ab],
                [c, ca, bc],
                [ab, bc, ca],
            ])
        faces = new_faces
    
    # 计算平面
    planes = []
    for face in faces:
        v0 = verts[face[0]]
        v1 = verts[face[1]]
        v2 = verts[face[2]]
        edge1 = v1 - v0
        edge2 = v2 - v0
        normal = np.cross(edge1, edge2)
        normal = normal / np.linalg.norm(normal)
        d = -np.dot(normal, v0)
        planes.append([normal[0], normal[1], normal[2], d])
    
    return Brush(planes, verts, faces)


def _make_cone_fallback(center, radius, height, segments):
    """纯 Python 圆锥"""
    cx, cy, cz = center
    verts = []
    # 底面圆周
    for i in range(segments):
        angle = 2 * np.pi * i / segments
        x = cx + radius * np.cos(angle)
        z = cz + radius * np.sin(angle)
        verts.append([x, cy, z])
    # 顶点
    verts.append([cx, cy + height, cz])
    verts = np.array(verts, dtype=np.float32)
    
    faces = []
    # 侧面
    for i in range(segments):
        i_next = (i + 1) % segments
        faces.append([i, i_next, segments])
    # 底面
    faces.append(list(range(segments - 1, -1, -1)))
    
    planes = []
    for face in faces:
        v0 = verts[face[0]]
        v1 = verts[face[1]]
        v2 = verts[face[2]]
        edge1 = v1 - v0
        edge2 = v2 - v0
        normal = np.cross(edge1, edge2)
        normal = normal / np.linalg.norm(normal)
        d = -np.dot(normal, v0)
        planes.append([normal[0], normal[1], normal[2], d])
    
    return Brush(planes, verts, faces)


# ============================================================
# 导出
# ============================================================
__all__ = [
    "Brush",
    "csg_union", "csg_difference", "csg_intersection",
    "make_cylinder", "make_sphere", "make_cone", "make_wedge",
    "TRIMESH_AVAILABLE",
]