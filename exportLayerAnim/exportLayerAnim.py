from krita import *
import os
import json
from . import exportLayerAnimDialog


class ExportLayerAnim(Extension):

    def __init__(self, parent):
        super(ExportLayerAnim, self).__init__(parent)

        # Png save info
        self.pngInfo = InfoObject()
        self.pngInfo.setProperty("alpha", True)
        self.pngInfo.setProperty("compression", 3)
        self.pngInfo.setProperty("forceSRGB", False)
        self.pngInfo.setProperty("indexed", False)
        self.pngInfo.setProperty("interlaced", False)
        self.pngInfo.setProperty("saveSRGBProfile", False)
        self.pngInfo.setProperty("transparencyFillcolor", [0,0,0])

        # Jpg save info
        self.jpgInfo = InfoObject()
        self.jpgInfo.setProperty('quality', 85)           # 0 (high compression/low quality) to 100 (low compression/higher quality)
        self.jpgInfo.setProperty('smoothing', 0)          # 0 to 100    
        self.jpgInfo.setProperty('subsampling', 0)        # 0=4:2:0 (smallest file size)   1=4:2:2    2=4:4:0     3=4:4:4 (Best quality)
        self.jpgInfo.setProperty('progressive', False)
        self.jpgInfo.setProperty('optimize', True)
        self.jpgInfo.setProperty('saveProfile', True)
        self.jpgInfo.setProperty("transparencyFillcolor", [0,0,0])

    # Krita.instance() exists, so do any setup work
    def setup(self):
        pass

    def mkdir(self, directory):
        if (os.path.exists(directory) and os.path.isdir(directory)):
            return

        try:
            os.makedirs(directory)
        except OSError as e:
            raise e

    def isLayerAnimated(self, node):
        if self.firstFrame:
            return False
        if node.animated():
            return True
        elif node.type() == "grouplayer":
            child = node.childNodes()
            for c in child:
                if c.animated():
                    return True
                elif c.type() == "grouplayer" and self.isLayerAnimated(c):
                    return True
        return False

    def hasKeyframeAtTime(self, node, i):
        if node.hasKeyframeAtTime(i):
            return True
        elif node.type() == "grouplayer":
            child = node.childNodes()
            for c in child:
                if c.hasKeyframeAtTime(i):
                    return True
                elif c.type() == "grouplayer" and self.isLayerAnimated(c):
                    return True
        return False

    def getCompositionIdx(self, name):
        # Get composition docker, view, model
        docker = next(d for d in Application.dockers() if d.objectName() == 'CompositionDocker')
        view = docker.findChild(QListView, 'compositionView')
        model = view.model()

        # Get a composition based on its name
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            text = index.data(role=Qt.DisplayRole)
            if text == name:
                return index
        else:
            raise RuntimeError(f'Unknown composition. (did get: {name=})')

    # A generator activating compositions one by one then returning to the previous state
    def composition(self):
        if not self.useCompositions:
            yield ""
            return

        # Get composition docker
        docker = next(d for d in Application.dockers() if d.objectName() == 'CompositionDocker')
        view = docker.findChild(QListView, 'compositionView')
        model = view.model()
        
        meta = docker.metaObject()
        activated = meta.method(meta.indexOfSlot(b'activated(QModelIndex)'))

        # Save current state
        saveClicked = meta.method(meta.indexOfSlot(b'saveClicked()'))
        saveClicked.invoke(docker)

        try:
            # Browse all composition
            for row in range(model.rowCount() - 1): # -1 because of the saved compo
                index = model.index(row, 0)
                checked_state = index.data(role=Qt.CheckStateRole)
                if checked_state == Qt.Checked:
                    # Activate the composition  
                    activated.invoke(docker, Q_ARG("QModelIndex", index))
                    yield index.data(role=Qt.DisplayRole)

        finally:
            lastIdx = model.index(model.rowCount() - 1, 0)

            # Reverse to the previous state
            activated.invoke(docker, Q_ARG("QModelIndex", lastIdx))

            # Remove the tmp save
            selectionModel = view.selectionModel()
            selectionModel.setCurrentIndex(lastIdx, QItemSelectionModel.Current)
            deleteClicked = meta.method(meta.indexOfSlot(b'deleteClicked()'))
            deleteClicked.invoke(docker)

    def initForExport(self):
        self.doc = Application.activeDocument()
        if not self.doc: return
        
        self.layersName = ""

        p = os.path.split(self.doc.fileName())
        self.exportPath = p[0]
        filename = os.path.splitext(p[1])[0]
        path = self.exportPath + "/" + filename
        if (os.path.exists(path) and os.path.isdir(path)):
            self.exportDir = filename
            self.namePrefix = ""
        else:
            self.exportDir = ""
            self.namePrefix = filename
        self.extension = "png"
        self.useCompositions = False
        self.firstFrame = False

    def isNodeEffectivelyVisible(self, node):
        if not node.visible():
            return False
        parent = node.parentNode()
        while parent:
            if not parent.visible():
                return False
            parent = parent.parentNode()
        return True

    def export(self):
        Application.setBatchmode(True)
        
        num_frames = self.doc.animationLength()
        num_digits = len(str(num_frames))
        fps = self.doc.framesPerSecond()

        # Create the folder if missing
        self.exportPath = self.exportPath + "/" + self.exportDir
        self.mkdir(self.exportPath)

        # Structure initiale du JSON
        json_output = {
            "version": "1.0",
            "project": {
                "name": self.doc.name(),
                "width": self.doc.width(),
                "height": self.doc.height(),
                "fps": fps,
                "frame_count": num_frames
            },
            "layers": []
        }
        
        haveAnimatedLayers = False
        for compo in self.composition():
            topLevelLayers = self.doc.topLevelNodes()
            animatedLayers = []
            
            for node in topLevelLayers:
                if (node.type() not in ["paintlayer", "clonelayer", "grouplayer", "filelayer", "vectorlayer"]):
                    continue
                if not self.isNodeEffectivelyVisible(node):
                    continue
                if "NE" in node.name() or node.name() == "No Name":
                    continue
                    
                if node.type() == "grouplayer" and "EC" in node.name():
                    child = node.childNodes()
                    for c in child:
                        topLevelLayers.append(c)
                else:
                    if self.isLayerAnimated(node):
                        # On prépare la structure de calque attendue par le script JSX
                        new_layer_data = {
                            "name": node.name(),
                            "visible": True,
                            "opacity": 255,
                            "instances": [] # C'est ici que le script AE lira le timing
                        }
                        json_output["layers"].append(new_layer_data)
                        
                        # 3. On l'ajoute à la liste de suivi pour la boucle de temps
                        animatedLayers.append({
                            'node': node,
                            'frame_count': 0,
                            'last_file': "", 
                            'instances_list': new_layer_data["instances"]
                        })
                    else: 
                        self.exportLayer(node, compo)

            # Export animated layers
            if len(animatedLayers) > 0:
                haveAnimatedLayers = True
                for i in range(num_frames):
                    self.doc.setCurrentTime(i)

                    for layer in animatedLayers:
                        node_name = layer['node'].name()
                        
                        # Détection d'une nouvelle image clé
                        if self.hasKeyframeAtTime(layer['node'], i):
                            suffix = "_" + str(layer['frame_count']).zfill(num_digits)
                            self.exportLayer(layer['node'], compo, suffix)
                            
                            layer['last_file'] = node_name + suffix + ".png"
                            layer['frame_count'] += 1

                        # On enregistre la frame (comble les vides avec last_file)
                        # On utilise 'instances_ref' qui pointe vers le JSON
                        layer['instances_list'].append({
                            "frame": i,
                            "link": layer['last_file']
                        })

        # Écriture du fichier JSON
        json_file_path = os.path.join(self.exportPath, "import_ae.json")
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(json_output, f, indent=4)
            
        # Undo the setCurrentTime
        if haveAnimatedLayers and num_frames > 0:
            Application.action('edit_undo').trigger()
            self.doc.save()
            
        Application.setBatchmode(False)
    
    # Create a file for the specified layer
    def exportLayer(self, node, prefix = "", suffix = ""):
        self.doc.waitForDone()
        fileName = f'{self.namePrefix}{prefix}{"_" if (self.namePrefix != "" or prefix != "") and node.name() != "" else ""}{node.name()}{suffix}.{self.extension}'
        self.layersName += "\n" + fileName
        path = self.exportPath + "/" + fileName
        bounds = QRect(0, 0, self.doc.width(), self.doc.height())
        if self.extension == "png":
            node.save(path, self.doc.resolution() / 72., self.doc.resolution() / 72., self.pngInfo, bounds)
        else:
            node.save(path, self.doc.resolution() / 72., self.doc.resolution() / 72., self.jpgInfo, bounds)

    # Show a dialogue asking for the folder name and files prefix
    def exportDialog(self):
        self.initForExport()
        if not self.doc: return
        
        self.mainDialog = exportLayerAnimDialog.ExportLayerAnimDialog(self, Application.activeWindow().qwindow())
        self.mainDialog.initialize()

    # called after setup(self)
    def createActions(self, window):
        action = window.createAction("export_layer_anim", i18n("Export layer anim"))
        action.setToolTip(i18n("Plugin to manipulate properties of selected documents."))
        action.triggered.connect(self.exportDialog)