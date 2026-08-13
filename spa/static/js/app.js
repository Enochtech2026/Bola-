/* Simple E-library SPA using LocalStorage for persistence */
(function(){
  const STORAGE_KEY = 'elib_books_v1'

  // Optional: if you host the Flask backend and want the SPA to use it,
  // set `backendBaseUrl` to the origin (e.g. 'http://127.0.0.1:5000')
  // When set, the SPA will try to load books from `/api/books`.
  const backendBaseUrl = null // e.g. 'http://127.0.0.1:5000'

  // DOM
  const listEl = document.getElementById('list')
  const searchEl = document.getElementById('search')
  const btnAdd = document.getElementById('btn-add')
  const dialog = document.getElementById('book-dialog')
  const form = document.getElementById('book-form')
  const inputId = document.getElementById('book-id')
  const inputTitle = document.getElementById('title')
  const inputAuthor = document.getElementById('author')
  const inputYear = document.getElementById('year')
  const inputIsbn = document.getElementById('isbn')
  const inputDesc = document.getElementById('description')
  const btnExport = document.getElementById('btn-export')
  const btnImport = document.getElementById('btn-import')
  const importFile = document.getElementById('import-file')

  let books = []

  function load(){
    // If backend configured, try to fetch books from API
    if(backendBaseUrl){
      fetch(backendBaseUrl + '/api/books').then(r=>r.json()).then(data=>{
        books = Array.isArray(data) ? data : []
        save()
      }).catch(err=>{
        console.warn('Failed to fetch from backend, falling back to LocalStorage', err)
        try{ books = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') || [] }catch(e){ books = [] }
        if(!books.length) seed()
      })
      return
    }
    try{
      books = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') || []
    }catch(e){ books = [] }
    if(!books.length){ seed() }
  }

  function seed(){
    books = [
      {id: Date.now()+1, title:'Clean Code', author:'Robert C. Martin', year:2008, isbn:'9780132350884', description:'Guidelines for writing clean code.'},
      {id: Date.now()+2, title:'The Pragmatic Programmer', author:'Andrew Hunt', year:1999, isbn:'9780201616224', description:'Software craftsmanship.'},
      {id: Date.now()+3, title:'Intro to Algorithms', author:'Cormen et al.', year:2009, isbn:'9780262033848', description:'Algorithms textbook.'}
    ]
    save()
  }

  function save(){
    localStorage.setItem(STORAGE_KEY, JSON.stringify(books))
    render()
  }

  function render(){
    const q = (searchEl.value || '').toLowerCase().trim()
    const filtered = books.filter(b=>{
      if(!q) return true
      return (b.title||'').toLowerCase().includes(q) || (b.author||'').toLowerCase().includes(q) || (b.isbn||'').toLowerCase().includes(q) || (b.description||'').toLowerCase().includes(q)
    }).sort((a,b)=> (a.title||'').localeCompare(b.title||''))

    listEl.innerHTML = ''
    if(!filtered.length){ listEl.innerHTML = '<p class="small">No books found.</p>'; return }

    for(const book of filtered){
      const card = document.createElement('article')
      card.className = 'card'
      card.innerHTML = `
        <h4>${escapeHtml(book.title)}</h4>
        <div class="meta">${escapeHtml(book.author)} ${book.year? '— '+book.year : ''}</div>
        <div class="small">${book.isbn? 'ISBN: '+escapeHtml(book.isbn): ''}</div>
        <div class="small">${escapeHtml(book.description || '')}</div>
        <div class="actions">
          <button class="btn" data-action="view" data-id="${book.id}">View</button>
          <button class="btn" data-action="edit" data-id="${book.id}">Edit</button>
          <button class="btn" data-action="del" data-id="${book.id}">Delete</button>
        </div>
      `
      listEl.appendChild(card)
    }
  }

  function escapeHtml(s){ return String(s || '').replace(/[&<>"']/g, c=> ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"})[c]) }

  // events
  searchEl.addEventListener('input', render)

  btnAdd.addEventListener('click', ()=>{
    inputId.value = ''
    form.reset()
    dialog.showModal()
    document.getElementById('form-title').textContent = 'Add Book'
  })

  listEl.addEventListener('click', e=>{
    const btn = e.target.closest('button')
    if(!btn) return
    const id = Number(btn.dataset.id)
    const action = btn.dataset.action
    const book = books.find(b=>b.id===id)
    if(action==='view'){
      alert(`${book.title}\n\nAuthor: ${book.author}${book.year? '\nYear: '+book.year : ''}${book.isbn? '\nISBN: '+book.isbn : ''}\n\n${book.description||''}`)
    }else if(action==='edit'){
      inputId.value = book.id
      inputTitle.value = book.title
      inputAuthor.value = book.author
      inputYear.value = book.year||''
      inputIsbn.value = book.isbn||''
      inputDesc.value = book.description||''
      document.getElementById('form-title').textContent = 'Edit Book'
      dialog.showModal()
    }else if(action==='del'){
      if(confirm('Delete this book?')){
        books = books.filter(b=>b.id!==id); save()
      }
    }
  })

  form.addEventListener('submit', e=>{
    e.preventDefault()
    const id = inputId.value ? Number(inputId.value) : null
    const title = inputTitle.value.trim()
    const author = inputAuthor.value.trim()
    const year = inputYear.value ? Number(inputYear.value) : null
    const isbn = inputIsbn.value.trim()
    const description = inputDesc.value.trim()
    if(!title || !author){ alert('Title and author are required'); return }
    if(id){
      const book = books.find(b=>b.id===id)
      if(!book) return
      book.title = title; book.author = author; book.year = year; book.isbn = isbn; book.description = description
    }else{
      books.push({id: Date.now(), title, author, year, isbn, description})
    }
    save()
    dialog.close()
  })

  document.getElementById('cancel').addEventListener('click', ()=> dialog.close())

  btnExport.addEventListener('click', ()=>{
    const blob = new Blob([JSON.stringify(books, null, 2)], {type:'application/json'})
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = 'elib-export.json'; a.click(); URL.revokeObjectURL(url)
  })

  btnImport.addEventListener('click', ()=> importFile.click())
  importFile.addEventListener('change', async e=>{
    const f = e.target.files[0]; if(!f) return
    try{
      const txt = await f.text(); const imported = JSON.parse(txt)
      if(Array.isArray(imported)){
        // merge without duplicates by isbn+title
        for(const it of imported){
          if(!it.title || !it.author) continue
          const exists = books.some(b=> (b.isbn && it.isbn && b.isbn===it.isbn) || (b.title===it.title && b.author===it.author))
          if(!exists) books.push(Object.assign({id: Date.now()+Math.random()}, it))
        }
        save();
        alert('Import complete')
      }else alert('Invalid file format')
    }catch(err){ alert('Failed to import: '+err.message) }
    importFile.value = ''
  })

  // init
  load(); render();
  // expose for debugging
  window.elib = {books, save}

})();
