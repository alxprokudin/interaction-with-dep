const BASE_URL = "https://mnogo-lososya-centralniy-of-co.iiko.it:443/resto/api/"

function login() {
  const params = {'login': 'api_reader', 'pass': '094ecebba43f460a904444a90e252e2ef4176711'};
  const queryString = Object.keys(params).map(key => key + '=' + params[key]).join('&');
  const authUrl = BASE_URL + 'auth?' + queryString;
  const response = UrlFetchApp.fetch(authUrl);
  const content = response.getContentText()
  console.log(content)
  return content;
}

function logout(token) {
  const params = {'key': token};
  const queryString = Object.keys(params).map(key => key + '=' + params[key]).join('&');
  const authUrl = BASE_URL + 'logout?' + queryString;
  const response = UrlFetchApp.fetch(authUrl);
  const content = response.getContentText();
  console.log(content)
  return content
}

function getProducts() {

    const productTable = SpreadsheetApp.openByUrl("https://docs.google.com/spreadsheets/d/1iBSyhBaMGwZU8l6LciqgHrxzSjj9jAK30RGz_7bPBwM/edit#gid=1598300754")
    const productIdTableSheet = productTable.getSheetByName("products_id")
  
    const token = login('api_reader', '094ecebba43f460a904444a90e252e2ef4176711')
    try {
  
      let response = getProductsFromApi(token)
      let xml = response.getContentText();
      let documents = XmlService.parse(xml);
      let root = documents.getRootElement();
  
      // console.log(documents)
      console.log(documents)
      console.log(xml)
  
      let document = root.getChildren('productDto');
  
      let resultArray = []
      document.forEach(item => {
        try {
          let name = item.getChild('name').getText();
          let id = item.getChild('id').getText();
          let num = item.getChild('num').getText();
          let productType = item.getChild('productType').getText();
          // console.log(productType)
          let cookingPlaceType = item.getChild('cookingPlaceType').getText();
          let mainUnit = item.getChild('mainUnit').getText();
          let productCategory = item.getChild('productCategory').getText();
          resultArray.push([num, name, id, productType, cookingPlaceType, mainUnit, productCategory])
        } catch (err) {
          // console.log(err)
        }   
      });
  
      console.log(resultArray)
      // const valuesForTable = resultArray.filter(item => item[3] == 'GOODS') // item[3] === 'GOODS' || 
  
      // console.log(valuesForTable)
  
      try {
        productIdTableSheet.getRange(2, 1, productIdTableSheet.getLastRow() - 1, productIdTableSheet.getLastColumn()).clearContent()
      } catch (error) {
        console.log(error)
      }
  
      productIdTableSheet.getRange(productIdTableSheet.getLastRow() + 1, 1, resultArray.length, resultArray[0].length).setValues(resultArray)
  
    } catch(error) {
      console.error('Error occurred:', error)
    } finally {
      logout(token)
    }
  }

  function responseToServer(date_from, date_to, token, sapID = []) {

    const filter_dict = {
      'Account.Name': ['Задолженность перед поставщиками'], 
      'Account.CounteragentType': ['SUPPLIER', 'INTERNAL_SUPPLIER'],
      'TransactionType': ['INVOICE'],
      'Contr-Product.Type': ['GOODS'],
      'Department.Code': sapID
    }
  
      const group_fields = [ 
        'Product.Id', 
        'Contr-Product.Name', 
        'Department.Code', 
        'Contr-Product.Num', 
        'Counteragent.Name', 
        'Contr-Product.MeasureUnit', 
        'Contr-Product.TopParent',
        'Contr-Product.SecondParent',
        'Department',
        'DateTime.Month',
        'DateTime.Year',
        'DateTime.DateTyped'
      ] 
    
      const agg_fields = ['Contr-Amount', 'Sum.ResignedSum'] // 'Amount', 
    
      let filters = {
        'DateTime.DateTyped': {
          'filterType': 'DateRange',
          'periodType': 'CUSTOM',
          'from': date_from,
          'to': date_to,
          'includeLow': true,
          'includeHigh': true
        }
      }
    
      for (var k in filter_dict){
        filters[k] = {
          'filterType': 'IncludeValues',
          'values': filter_dict[k],
        }
      }
  
      // console.log('Filters',filters)
    
      const params = {
        'reportType': 'TRANSACTIONS',
        'buildSummary': false,
        'groupByColFields': group_fields,
        'aggregateFields': agg_fields,
        'filters': filters
      }
  
      const url = BASE_URL + 'v2/reports/olap?key=' + token
          
      const options = {
        'method': 'get',
        'payload': JSON.stringify(params),
        'headers': {'Content-type': 'Application/json; charset=utf-8'}
      };
      const response = UrlFetchApp.fetch(url, options);
      const content = JSON.parse(response.getContentText());
          
      return content;
    }
  


function responseToServerWithProducts(date_from, date_to, token, sapID, productsName) {
  
    const filter_dict = {
      'Account.Name': ['Задолженность перед поставщиками'], 
      'Account.CounteragentType': ['SUPPLIER', 'INTERNAL_SUPPLIER'],
      'TransactionType': ['INVOICE'],
      'Contr-Product.Type': ['GOODS'],
      'Department.Code': sapID,
      'Contr-Product.Name': productsName
    }
  
      const group_fields = [ 
        'Product.Id', 
        'Contr-Product.Name', 
        'Department.Code', 
        'Contr-Product.Num', 
        'Counteragent.Name', 
        'Contr-Product.MeasureUnit', 
        'Contr-Product.TopParent',
        'Contr-Product.SecondParent',
        'Department',
        'DateTime.Month',
        'DateTime.Year',
        'DateTime.DateTyped'
      ] 
    
      const agg_fields = ['Contr-Amount', 'Sum.ResignedSum'] // 'Amount', 
    
      let filters = {
        'DateTime.DateTyped': {
          'filterType': 'DateRange',
          'periodType': 'CUSTOM',
          'from': date_from,
          'to': date_to,
          'includeLow': true,
          'includeHigh': true
        }
      }
    
      for (var k in filter_dict){
        filters[k] = {
          'filterType': 'IncludeValues',
          'values': filter_dict[k],
        }
      }
  
      // console.log('Filters',filters)
    
      const params = {
        'reportType': 'TRANSACTIONS',
        'buildSummary': false,
        'groupByColFields': group_fields,
        'aggregateFields': agg_fields,
        'filters': filters
      }
  
      const url = BASE_URL + 'v2/reports/olap?key=' + token
          
      const options = {
        'method': 'get',
        'payload': JSON.stringify(params),
        'headers': {'Content-type': 'Application/json; charset=utf-8'}
      };
      const response = UrlFetchApp.fetch(url, options);
      const content = JSON.parse(response.getContentText());
          
      return content;
    }

    function myFunction1() {

      const storeTable = SpreadsheetApp.openByUrl("https://docs.google.com/spreadsheets/d/1aSMppOuMrVPB-Y-8qmikxVz6BSSPhRN3cQqup015lrA/edit?gid=0#gid=0")
      const storeTableSheetDepartments = storeTable.getSheetByName("ДЕПАРТАМЕНТЫ_IIKO")
    
      // Очистка старого содержимого
      try {
        storeTableSheetDepartments.getRange(2, 1, storeTableSheetDepartments.getLastRow() - 1, storeTableSheetDepartments.getLastColumn()).clearContent()
      } catch(error) {
        console.log(error)
      }
    
      const token = login('api_reader', '094ecebba43f460a904444a90e252e2ef4176711')
      try {
    
        let response = getDepartmentsId(token)
        let xml = response.getContentText();
        let documents = XmlService.parse(xml);
        let root = documents.getRootElement();
    
        console.log(xml)
    
        let document = root.getChildren('corporateItemDto');
    
        console.log(document)
    
        let resultArray = []
        document.forEach(item => {
          try {
            let id = item.getChild('id').getText();
            let parentId = item.getChild('parentId').getText();
            let code = item.getChild('code').getText();
            let name = item.getChild('name').getText();
            let type = item.getChild('type').getText();
            resultArray.push([id, parentId, code, name, type])
          } catch (err) {
            // console.log(err)
          }   
        });
    
        // console.log(resultArray)
    
        // console.log(valuesForTable)
    
        storeTableSheetDepartments.getRange(2, 1, resultArray.length, resultArray[0].length).setValues(resultArray)
    
      } catch(error) {
        console.error('Error occurred:', error)
      } finally {
        logout(token)
      }
      
      
    }

    function getDepartmentsId(token) {
      const params = {
        'key': token,
        // 'productType': 'G'
      };
      const queryString = Object.keys(params).map(key => key + '=' + params[key]).join('&');
      const authUrl = BASE_URL + 'corporation/departments?' + queryString;
      console.log(authUrl)
      const response = UrlFetchApp.fetch(authUrl);
      const content = response //.getContentText();
      console.log(content);
      return content
    }