// Recebe a nota e a etiqueta de cada pedido da Annis e salva na pasta de
// envios do Drive, em "Nome da cliente / #pedido · data". 09/10/2026.
//
// Fica num projeto do Apps Script (script.google.com) na conta que tem a
// pasta, implantado como App da Web ("Executar como: eu", "Qualquer pessoa").
// Quem chama é avisos/drive.py, com o token abaixo; sem o token certo, nada é
// salvo. A versão deste arquivo no GitHub vai sem o token: o de verdade só
// existe no projeto do Apps Script e nos segredos do GitHub (DRIVE_TOKEN).

const PASTA_ENVIOS = '1C4Nyn8bLdxwAhz3dR4rLwofQCWDaPuvL';
const TOKEN = 'COLE_AQUI_O_TOKEN';

function doPost(e) {
  const req = JSON.parse(e.postData.contents);
  if (req.token !== TOKEN) return resposta({ ok: false, erro: 'token' });
  let pasta = DriveApp.getFolderById(PASTA_ENVIOS);
  for (const nome of req.caminho) {
    const achadas = pasta.getFoldersByName(nome);
    pasta = achadas.hasNext() ? achadas.next() : pasta.createFolder(nome);
  }
  // Mesmo nome de arquivo na mesma pasta: a versão antiga vai para a lixeira.
  const antigos = pasta.getFilesByName(req.nome);
  while (antigos.hasNext()) antigos.next().setTrashed(true);
  const arquivo = pasta.createFile(Utilities.newBlob(
    Utilities.base64Decode(req.base64), req.tipo || 'application/pdf', req.nome));
  return resposta({ ok: true, id: arquivo.getId(), url: arquivo.getUrl() });
}

function resposta(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
